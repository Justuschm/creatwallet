"""Kunden-Portal für Firmen: Übersicht, Vorlagen, Pässe, Einbinden, Statistik, Team, Einstellungen."""

import base64
import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from creatwallet.templates import TEMPLATES

from .. import placeholders, services
from ..api.deps import ctx, get_session
from ..models import ApiKey, Invitation, Pass, Registration, Template, User, WebhookEndpoint, utcnow
from ..services import ServiceError, issues_json
from .core import ROLES, audit, check_csrf, flash, portal_user, render, require

router = APIRouter(prefix="/portal", include_in_schema=False, dependencies=[Depends(check_csrf)])
PKPASS = "application/vnd.apple.pkpass"


def back(url, request=None, message=None, kind="ok"):
    if request is not None and message:
        flash(request, message, kind)
    return RedirectResponse(url, status_code=303)


def _template(session, user, template_id):
    return services.get_template(session, user.tenant, template_id)


def _pass(session, user, pass_id):
    return services.get_pass(session, user.tenant, pass_id)


# ---------------------------------------------------------------- Übersicht

@router.get("")
def dashboard(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    tid = user.tenant_id
    since = utcnow() - timedelta(days=30)
    stats = {
        "active": session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == tid, Pass.status == "active")),
        "installed": session.scalar(select(func.count(Registration.id)).join(Pass)
                                    .where(Pass.tenant_id == tid)),
        "issued_30": session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == tid, Pass.created_at >= since)),
        "templates": session.scalar(select(func.count(Template.id)).where(Template.tenant_id == tid)),
    }
    pending = [t for t in services.list_templates(session, user.tenant) if t.latest_version.status == "pending"]
    return render(request, "portal/dashboard.html", user, stats=stats, pending=pending, tenant=user.tenant)


# ---------------------------------------------------------------- Vorlagen

@router.get("/templates")
def templates_list(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    bases = sorted(((k, t["title"]) for k, t in TEMPLATES.items()), key=lambda x: x[1])
    return render(request, "portal/templates.html", user, templates=services.list_templates(session, user.tenant),
                  bases=bases)


@router.post("/templates")
def template_create(request: Request, name: str = Form(...), base: str = Form(...),
                    user: User = Depends(portal_user), session: Session = Depends(get_session)):
    require(user, "templates")
    if base not in TEMPLATES:
        return back("/portal/templates", request, "Unbekannte Startvorlage.", "error")
    from creatwallet.templates import new_pass, placeholder_images

    settings = ctx(request)[0]
    try:
        t, _ = services.create_template(session, user.tenant, name.strip(), new_pass(base),
                                        placeholder_images(base, scales=(2, 3)), settings)
    except ServiceError as exc:
        return back("/portal/templates", request, exc.message, "error")
    audit(session, user, "template.create", user.tenant_id, t.id, name=t.name)
    session.commit()
    return back(f"/portal/templates/{t.id}/editor", request, "Vorlage angelegt - jetzt gestalten.")


@router.get("/templates/{template_id}")
def template_detail(template_id: str, request: Request, user: User = Depends(portal_user),
                    session: Session = Depends(get_session)):
    t = _template(session, user, template_id)
    latest = t.latest_version
    issues = services.preview_issues(user.tenant, latest.pass_json, latest.file_map())
    return render(request, "portal/template_detail.html", user, t=t, latest=latest, issues=issues,
                  placeholders=placeholders.find(latest.pass_json),
                  passes=session.scalar(select(func.count(Pass.id)).where(Pass.template_id == t.id)))


@router.post("/templates/{template_id}/test-pass")
def template_test_pass(template_id: str, request: Request, user: User = Depends(portal_user),
                       session: Session = Depends(get_session)):
    t = _template(session, user, template_id)
    try:
        data = services.build_test_pass(user.tenant, t.latest_version, None, ctx(request)[1])
    except ServiceError as exc:
        return back(f"/portal/templates/{t.id}", request, exc.message, "error")
    return Response(data, media_type=PKPASS, headers={"Content-Disposition": 'attachment; filename="test.pkpass"'})


# Editor: der creatwallet-Editor mit Speichern in die Plattform

@router.get("/templates/{template_id}/editor")
def editor(template_id: str, request: Request, user: User = Depends(portal_user),
           session: Session = Depends(get_session)):
    from importlib import resources

    t = _template(session, user, template_id)
    html = resources.files("creatwallet").joinpath("web/index.html").read_text(encoding="utf-8")
    config = {"base": f"/portal/templates/{t.id}/editor", "name": t.name, "back": f"/portal/templates/{t.id}",
              "csrf": request.session.get("csrf") or "", "canSave": "templates" in _perms(user)}
    inject = f"<script>window.CW_PLATFORM = {json.dumps(config)};</script>\n<script>"
    return Response(html.replace("<script>", inject, 1), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


def _perms(user):
    from .core import PERMISSIONS
    return PERMISSIONS.get(user.role, set())


@router.get("/templates/{template_id}/editor/data")
def editor_data(template_id: str, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    latest = _template(session, user, template_id).latest_version
    return {"pass": latest.pass_json,
            "images": {f.name: base64.b64encode(f.data).decode() for f in latest.files},
            "version": latest.number, "status": latest.status}


@router.post("/templates/{template_id}/editor/validate")
async def editor_validate(template_id: str, request: Request, user: User = Depends(portal_user),
                          session: Session = Depends(get_session)):
    _template(session, user, template_id)
    body = await request.json()
    images = {n: base64.b64decode(b) for n, b in (body.get("images") or {}).items()}
    issues = services.preview_issues(user.tenant, body.get("pass") or {}, images)
    return {"issues": issues_json(issues), "placeholders": placeholders.find(body.get("pass") or {})}


@router.post("/templates/{template_id}/editor/save")
async def editor_save(template_id: str, request: Request, user: User = Depends(portal_user),
                      session: Session = Depends(get_session)):
    require(user, "templates")
    t = _template(session, user, template_id)
    body = await request.json()
    try:
        images = {n: base64.b64decode(b) for n, b in (body.get("images") or {}).items()}
        _, version = services.update_template(session, t, None, body.get("pass"), images, ctx(request)[0])
    except (ServiceError, ValueError) as exc:
        session.rollback()
        return Response(json.dumps({"error": getattr(exc, "message", str(exc))}), status_code=422,
                        media_type="application/json")
    audit(session, user, "template.version", user.tenant_id, t.id, version=version.number)
    session.commit()
    return {"version": version.number, "status": version.status}


@router.post("/templates/{template_id}/editor/test-pass")
async def editor_test_pass(template_id: str, request: Request, user: User = Depends(portal_user),
                           session: Session = Depends(get_session)):
    """Test-Pass aus dem aktuellen Stand im Editor (ungespeichert)."""
    _template(session, user, template_id)
    body = await request.json()
    from ..models import TemplateFile, TemplateVersion

    images = {n: base64.b64decode(b) for n, b in (body.get("images") or {}).items()}
    draft = TemplateVersion(number=0, pass_json=services.clean_template_input(body.get("pass"), images),
                            files=[TemplateFile(name=n, data=d) for n, d in images.items()])
    try:
        data = services.build_test_pass(user.tenant, draft, None, ctx(request)[1])
    except ServiceError as exc:
        return Response(json.dumps({"error": exc.message, "issues": issues_json(exc.issues)}),
                        status_code=exc.status, media_type="application/json")
    return Response(data, media_type=PKPASS, headers={"Content-Disposition": 'attachment; filename="test.pkpass"'})


# ---------------------------------------------------------------- Pässe

@router.get("/passes")
def passes_list(request: Request, q: str = "", template_id: str = "", page: int = 1,
                user: User = Depends(portal_user), session: Session = Depends(get_session)):
    per_page = 50
    stmt = select(Pass).where(Pass.tenant_id == user.tenant_id)
    if template_id:
        stmt = stmt.where(Pass.template_id == template_id)
    if q.strip():
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Pass.serial_number.ilike(like), cast(Pass.data, Text).ilike(like)))
    items = session.scalars(stmt.order_by(Pass.created_at.desc()).limit(per_page + 1)
                            .offset((max(page, 1) - 1) * per_page)).all()
    return render(request, "portal/passes.html", user, items=items[:per_page], more=len(items) > per_page,
                  page=max(page, 1), q=q, template_id=template_id,
                  templates=services.list_templates(session, user.tenant))


@router.get("/passes/new")
def pass_new(request: Request, template_id: str = "", user: User = Depends(portal_user),
             session: Session = Depends(get_session)):
    require(user, "passes")
    usable = [t for t in services.list_templates(session, user.tenant) if t.approved_version]
    t = next((x for x in usable if x.id == template_id), usable[0] if usable else None)
    fields = placeholders.find(t.approved_version.pass_json) if t else []
    return render(request, "portal/pass_new.html", user, templates=usable, t=t, fields=fields)


@router.post("/passes")
async def pass_create(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    require(user, "passes")
    form = await request.form()
    template_id = str(form.get("template_id", ""))
    data = {k[2:]: str(val) for k, val in form.items() if k.startswith("f_")}
    settings, signers, vault = ctx(request)
    try:
        p, _ = services.create_pass(session, user.tenant, template_id, data, settings, signers, vault,
                                    serial_number=str(form.get("serial_number") or "").strip() or None)
    except ServiceError as exc:
        session.rollback()
        detail = "; ".join(i.message for i in exc.issues[:3])
        return back(f"/portal/passes/new?template_id={template_id}", request,
                    exc.message + (f" ({detail})" if detail else ""), "error")
    audit(session, user, "pass.create", user.tenant_id, p.id, serial=p.serial_number)
    session.commit()
    return back(f"/portal/passes/{p.id}", request, "Pass ausgegeben.")


@router.get("/passes/{pass_id}")
def pass_detail(pass_id: str, request: Request, user: User = Depends(portal_user),
                session: Session = Depends(get_session)):
    p = _pass(session, user, pass_id)
    settings = ctx(request)[0]
    page_url = f"{settings.public_base_url}/p/{p.download_token}"
    fields = placeholders.find(p.template.approved_version.pass_json) if p.template.approved_version else list(p.data)
    return render(request, "portal/pass_detail.html", user, p=p, page_url=page_url, fields=fields)


@router.post("/passes/{pass_id}")
async def pass_update(pass_id: str, request: Request, user: User = Depends(portal_user),
                      session: Session = Depends(get_session)):
    require(user, "passes")
    p = _pass(session, user, pass_id)
    form = await request.form()
    data = {k[2:]: str(val) for k, val in form.items() if k.startswith("f_")}
    changed = {k: val for k, val in data.items() if str(p.data.get(k, "")) != val}
    if not changed:
        return back(f"/portal/passes/{p.id}", request, "Keine Änderungen.")
    settings, signers, vault = ctx(request)
    try:
        services.update_pass(session, p, changed, settings, signers, vault)
    except ServiceError as exc:
        session.rollback()
        return back(f"/portal/passes/{pass_id}", request, exc.message, "error")
    audit(session, user, "pass.update", user.tenant_id, p.id, fields=sorted(changed))
    session.commit()
    return back(f"/portal/passes/{p.id}", request, f"Gespeichert - Version {p.version}. Geräte werden benachrichtigt.")


@router.post("/passes/{pass_id}/void")
def pass_void(pass_id: str, request: Request, user: User = Depends(portal_user),
              session: Session = Depends(get_session)):
    require(user, "passes")
    p = _pass(session, user, pass_id)
    services.void_pass(session, p)
    audit(session, user, "pass.void", user.tenant_id, p.id)
    session.commit()
    return back(f"/portal/passes/{p.id}", request, "Pass gesperrt.")


# ---------------------------------------------------------------- Einbinden (API-Schlüssel, Webhooks)

@router.get("/developers")
def developers(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    keys = session.scalars(select(ApiKey).where(ApiKey.tenant_id == user.tenant_id)
                           .order_by(ApiKey.created_at.desc())).all()
    hooks = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == user.tenant_id)
                            .order_by(WebhookEndpoint.created_at)).all()
    from ..jobs import EVENTS

    return render(request, "portal/developers.html", user, keys=keys, hooks=hooks, events=EVENTS,
                  new_key=request.session.pop("new_key", None), new_secret=request.session.pop("new_secret", None),
                  base=ctx(request)[0].public_base_url)


@router.post("/keys")
def key_create(request: Request, name: str = Form(""), user: User = Depends(portal_user),
               session: Session = Depends(get_session)):
    require(user, "keys")
    k, full = services.create_api_key(session, user.tenant, name.strip() or "Portal")
    audit(session, user, "apikey.create", user.tenant_id, k.prefix)
    session.commit()
    request.session["new_key"] = full
    return back("/portal/developers")


@router.post("/keys/{key_id}/revoke")
def key_revoke(key_id: str, request: Request, user: User = Depends(portal_user),
               session: Session = Depends(get_session)):
    require(user, "keys")
    k = session.get(ApiKey, key_id)
    if k is None or k.tenant_id != user.tenant_id:
        raise ServiceError(404, "Schlüssel nicht gefunden.")
    k.revoked_at = k.revoked_at or utcnow()
    audit(session, user, "apikey.revoke", user.tenant_id, k.prefix)
    session.commit()
    return back("/portal/developers", request, "Schlüssel widerrufen.")


@router.post("/webhooks")
async def webhook_create(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    require(user, "keys")
    form = await request.form()
    settings, _, vault = ctx(request)
    try:
        ep, secret = services.create_webhook(session, user.tenant, str(form.get("url", "")).strip(),
                                             [str(e) for e in form.getlist("events")], settings, vault)
    except ServiceError as exc:
        return back("/portal/developers", request, exc.message, "error")
    audit(session, user, "webhook.create", user.tenant_id, ep.id, url=ep.url)
    session.commit()
    request.session["new_secret"] = secret
    return back("/portal/developers")


@router.post("/webhooks/{hook_id}/{action}")
def webhook_action(hook_id: str, action: str, request: Request, user: User = Depends(portal_user),
                   session: Session = Depends(get_session)):
    require(user, "keys")
    ep = session.get(WebhookEndpoint, hook_id)
    if ep is None or ep.tenant_id != user.tenant_id:
        raise ServiceError(404, "Webhook nicht gefunden.")
    if action == "delete":
        session.delete(ep)
        audit(session, user, "webhook.delete", user.tenant_id, hook_id)
        message = "Webhook gelöscht."
    elif action == "test":
        services.send_test_webhook(session, ep)
        message = "Test-Ereignis wird zugestellt."
    else:
        raise ServiceError(404, "Unbekannte Aktion.")
    session.commit()
    return back("/portal/developers", request, message)


# ---------------------------------------------------------------- Statistik

@router.get("/stats")
def stats(request: Request, days: int = 30, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    days = min(max(days, 7), 365)
    start = (utcnow() - timedelta(days=days - 1)).date()
    issued = _per_day(session.scalars(select(Pass.created_at).where(Pass.tenant_id == user.tenant_id,
                                                                    Pass.created_at >= start)).all())
    installed = _per_day(session.scalars(select(Registration.created_at).join(Pass)
                                         .where(Pass.tenant_id == user.tenant_id,
                                                Registration.created_at >= start)).all())
    rows = []
    for i in range(days):
        d = start + timedelta(days=i)
        rows.append({"day": d, "issued": issued.get(d, 0), "installed": installed.get(d, 0)})
    peak = max([1] + [max(r["issued"], r["installed"]) for r in rows])
    per_template = session.execute(
        select(Template.name, func.count(Pass.id)).join(Pass, Pass.template_id == Template.id)
        .where(Template.tenant_id == user.tenant_id, Pass.status == "active").group_by(Template.name)).all()
    return render(request, "portal/stats.html", user, rows=rows, peak=peak, days=days, per_template=per_template,
                  total_issued=sum(r["issued"] for r in rows), total_installed=sum(r["installed"] for r in rows))


def _per_day(timestamps):
    out = {}
    for ts in timestamps:
        out[ts.date()] = out.get(ts.date(), 0) + 1
    return out


# ---------------------------------------------------------------- Team

@router.get("/team")
def team(request: Request, user: User = Depends(portal_user), session: Session = Depends(get_session)):
    members = session.scalars(select(User).where(User.tenant_id == user.tenant_id).order_by(User.created_at)).all()
    invites = session.scalars(select(Invitation).where(Invitation.tenant_id == user.tenant_id,
                                                       Invitation.accepted_at.is_(None))).all()
    return render(request, "portal/team.html", user, members=members, invites=invites,
                  base=ctx(request)[0].public_base_url, new_invite=request.session.pop("new_invite", None))


@router.post("/team/invite")
def team_invite(request: Request, email: str = Form(...), role: str = Form(...), user: User = Depends(portal_user),
                session: Session = Depends(get_session)):
    require(user, "team")
    if role not in ROLES or (role == "owner" and user.role != "owner"):
        return back("/portal/team", request, "Diese Rolle kannst du nicht vergeben.", "error")
    inv = Invitation(tenant_id=user.tenant_id, email=email.strip()[:200], role=role, token=secrets.token_urlsafe(24),
                     created_by=user.email, expires_at=utcnow() + timedelta(days=7))
    session.add(inv)
    audit(session, user, "team.invite", user.tenant_id, inv.id, email=inv.email, role=role)
    session.commit()
    request.session["new_invite"] = f"{ctx(request)[0].public_base_url}/invite/{inv.token}"
    return back("/portal/team")


@router.post("/team/{member_id}/role")
def team_role(member_id: str, request: Request, role: str = Form(...), user: User = Depends(portal_user),
              session: Session = Depends(get_session)):
    require(user, "team")
    member = session.get(User, member_id)
    if member is None or member.tenant_id != user.tenant_id or role not in ROLES:
        raise ServiceError(404, "Mitglied nicht gefunden.")
    if (member.role == "owner" or role == "owner") and user.role != "owner":
        return back("/portal/team", request, "Nur Inhaber können Inhaber-Rechte ändern.", "error")
    if member.role == "owner" and role != "owner" and _owner_count(session, user.tenant_id) <= 1:
        return back("/portal/team", request, "Es muss mindestens einen Inhaber geben.", "error")
    member.role = role
    audit(session, user, "team.role", user.tenant_id, member.id, role=role)
    session.commit()
    return back("/portal/team", request, f"{member.email}: {ROLES[role]}")


@router.post("/team/{member_id}/remove")
def team_remove(member_id: str, request: Request, user: User = Depends(portal_user),
                session: Session = Depends(get_session)):
    require(user, "team")
    member = session.get(User, member_id)
    if member is None or member.tenant_id != user.tenant_id:
        raise ServiceError(404, "Mitglied nicht gefunden.")
    if member.role == "owner" and (user.role != "owner" or _owner_count(session, user.tenant_id) <= 1):
        return back("/portal/team", request, "Der letzte Inhaber kann nicht entfernt werden.", "error")
    member.tenant_id, member.role = None, "viewer"
    audit(session, user, "team.remove", user.tenant_id, member.id, email=member.email)
    session.commit()
    if member.id == user.id:
        return back("/portal/onboarding")
    return back("/portal/team", request, f"{member.email} entfernt.")


@router.post("/invitations/{invite_id}/revoke")
def invite_revoke(invite_id: str, request: Request, user: User = Depends(portal_user),
                  session: Session = Depends(get_session)):
    require(user, "team")
    inv = session.get(Invitation, invite_id)
    if inv is None or inv.tenant_id != user.tenant_id:
        raise ServiceError(404, "Einladung nicht gefunden.")
    session.delete(inv)
    session.commit()
    return back("/portal/team", request, "Einladung zurückgezogen.")


def _owner_count(session, tenant_id):
    return session.scalar(select(func.count(User.id)).where(User.tenant_id == tenant_id, User.role == "owner"))


# ---------------------------------------------------------------- Einstellungen

@router.get("/settings")
def settings_page(request: Request, user: User = Depends(portal_user)):
    return render(request, "portal/settings.html", user, tenant=user.tenant)


@router.post("/settings")
def settings_save(request: Request, name: str = Form(...), organization_name: str = Form(...),
                  address: str = Form(""), vat_id: str = Form(""), contact_email: str = Form(""),
                  user: User = Depends(portal_user), session: Session = Depends(get_session)):
    require(user, "settings")
    t = user.tenant
    t.name, t.organization_name = name.strip()[:200] or t.name, organization_name.strip()[:200] or t.organization_name
    t.address, t.vat_id, t.contact_email = address.strip()[:1000], vat_id.strip()[:40], contact_email.strip()[:200]
    audit(session, user, "tenant.settings", t.id, t.id)
    session.commit()
    return back("/portal/settings", request, "Gespeichert.")

