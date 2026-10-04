"""Admin: Firmenbereich. Alles zu einer Firma an einem Ort, mit eigenem Menü.

Jede Seite hier lädt Daten ausschließlich über die Firmen-ID aus der URL - so können Daten
verschiedener Firmen nicht durcheinandergeraten.
"""

import base64
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from .. import placeholders, services
from ..api.deps import ctx, get_session
from ..certs import CertStoreError, import_certificate
from ..models import (ApiKey, AuditLog, Certificate, Invitation, Job, Pass, Registration, Template, Tenant, User,
                      WebhookEndpoint, utcnow)
from ..services import ServiceError
from .admin import PLANS, render
from .core import admin_user, audit, check_csrf, flash

router = APIRouter(prefix="/admin/tenants/{tenant_id}", include_in_schema=False, dependencies=[Depends(check_csrf)])

TABS = [("overview", "Übersicht", ""), ("certificate", "Zertifikat", "/certificate"), ("templates", "Vorlagen", "/templates"), ("passes", "Pässe", "/passes"),
        ("team", "Team", "/team"), ("integrations", "Integrationen", "/integrations"), ("jobs", "Jobs", "/jobs"),
        ("activity", "Aktivität", "/activity")]


def _counts(session, t):
    return {
        "templates": session.scalar(select(func.count(Template.id)).where(Template.tenant_id == t.id)),
        "passes": session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == t.id)),
        "team": session.scalar(select(func.count(User.id)).where(User.tenant_id == t.id)),
        "integrations": (session.scalar(select(func.count(ApiKey.id)).where(ApiKey.tenant_id == t.id,
                                                                             ApiKey.revoked_at.is_(None)))
                         + session.scalar(select(func.count(WebhookEndpoint.id))
                                          .where(WebhookEndpoint.tenant_id == t.id))),
        "jobs": session.scalar(select(func.count(Job.id)).where(Job.tenant_id == t.id, Job.status == "failed")),
    }


def page(request, user, session, t, tab, name, **extra):
    return render(request, f"admin/tenant/{name}.html", user, t=t, tab=tab, tabs=TABS, counts=_counts(session, t),
                  **extra)


def back(url, request=None, message=None, kind="ok"):
    if request is not None and message:
        flash(request, message, kind)
    return RedirectResponse(url, status_code=303)


def _own(obj, t, what):
    """Objekt gehört zu dieser Firma - sonst 404 (nie Daten einer anderen Firma zeigen)."""
    if obj is None or getattr(obj, "tenant_id", None) != t.id:
        raise ServiceError(404, f"{what} gehört nicht zu dieser Firma.")
    return obj


# ---------------------------------------------------------------- Übersicht

@router.get("")
def overview(tenant_id: str, request: Request, user: User = Depends(admin_user),
             session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    stats = {
        "active": session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == t.id, Pass.status == "active")),
        "installed": session.scalar(select(func.count(Registration.id)).join(Pass).where(Pass.tenant_id == t.id)),
        "voided": session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == t.id, Pass.status == "voided")),
    }
    log = session.scalars(select(AuditLog).where(AuditLog.tenant_id == t.id)
                          .order_by(AuditLog.created_at.desc()).limit(6)).all()
    from .. import plans as plan_defs

    return page(request, user, session, t, "overview", "overview", stats=stats, plans=PLANS, log=log, now=utcnow(),
                u=plan_defs.usage(session, t), plan_labels={k: p.label for k, p in plan_defs.PLANS.items()})


# ---------------------------------------------------------------- Zertifikat

@router.get("/certificate")
def certificate(tenant_id: str, request: Request, user: User = Depends(admin_user),
                session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    standard = session.scalars(select(Certificate).where(Certificate.owner_tenant_id.is_(None))
                               .order_by(Certificate.pass_type_identifier)).all()
    own = session.scalars(select(Certificate).where(Certificate.owner_tenant_id == t.id)
                          .order_by(Certificate.created_at.desc())).all()
    usage = dict(session.execute(select(Tenant.certificate_id, func.count(Tenant.id))
                                 .group_by(Tenant.certificate_id)).all())
    return page(request, user, session, t, "certificate", "certificate", standard=standard, own=own, usage=usage,
                now=utcnow())


@router.post("/certificate/upload")
async def certificate_upload(tenant_id: str, request: Request, p12: UploadFile = File(...), password: str = Form(""),
                             activate: bool = Form(False), user: User = Depends(admin_user),
                             session: Session = Depends(get_session)):
    """Eigenes Zertifikat der Firma (aus deren Apple-Account) hochladen."""
    t = services.tenant_by_id(session, tenant_id)
    settings, signers, _ = ctx(request)
    data = await p12.read()
    if len(data) > 100_000:
        return back(f"/admin/tenants/{t.id}/certificate", request, "Datei zu groß für ein .p12-Zertifikat.", "error")
    try:
        cert = import_certificate(session, signers.store, data, password, settings.wwdr_path, owner_tenant_id=t.id)
    except CertStoreError as exc:
        session.rollback()
        return back(f"/admin/tenants/{t.id}/certificate", request, str(exc), "error")
    signers.forget(cert.pass_type_identifier)
    if activate and services.assign_certificate(session, t, cert):
        flash(request, "Hinweis: " + services.SWITCH_HINT, "warn")
    audit(session, user, "admin.certificate.own.import", t.id, cert.id, pass_type_identifier=cert.pass_type_identifier,
          activated=activate)
    session.commit()
    return back(f"/admin/tenants/{t.id}/certificate", request,
                f"Eigenes Zertifikat {cert.pass_type_identifier} gespeichert" + (" und aktiviert." if activate else "."))


# ---------------------------------------------------------------- Vorlagen

@router.get("/templates")
def templates(tenant_id: str, request: Request, user: User = Depends(admin_user),
              session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    usage = dict(session.execute(select(Pass.template_id, func.count(Pass.id)).where(Pass.tenant_id == t.id)
                                 .group_by(Pass.template_id)).all())
    return page(request, user, session, t, "templates", "templates", templates=services.list_templates(session, t),
                usage=usage)


@router.get("/templates/{template_id}")
def template_detail(tenant_id: str, template_id: str, request: Request, user: User = Depends(admin_user),
                    session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    tpl = services.get_template(session, t, template_id)
    version = tpl.approved_version or tpl.latest_version
    images = {f.name: base64.b64encode(f.data).decode() for f in version.files}
    issues = services.preview_issues(t, version.pass_json, version.file_map())
    return page(request, user, session, t, "templates", "template_detail", tpl=tpl, version=version, images=images,
                issues=issues, placeholders=placeholders.find(version.pass_json),
                pass_json=json.dumps(version.pass_json, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- Pässe

@router.get("/passes")
def passes(tenant_id: str, request: Request, q: str = "", page_no: int = 1, user: User = Depends(admin_user),
           session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    stmt = select(Pass).where(Pass.tenant_id == t.id)
    if q.strip():
        like = f"%{q.strip()[:100]}%"
        stmt = stmt.where(or_(Pass.serial_number.ilike(like), cast(Pass.data, Text).ilike(like)))
    rows = session.scalars(stmt.order_by(Pass.created_at.desc()).limit(51).offset((max(page_no, 1) - 1) * 50)).all()
    return page(request, user, session, t, "passes", "passes", items=rows[:50], more=len(rows) > 50, q=q,
                page_no=max(page_no, 1))


@router.get("/passes/{pass_id}")
def pass_detail(tenant_id: str, pass_id: str, request: Request, user: User = Depends(admin_user),
                session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    p = services.get_pass(session, t, pass_id)
    pass_jobs = [j for j in session.scalars(select(Job).where(Job.tenant_id == t.id, Job.kind == "push")
                                              .order_by(Job.created_at.desc()).limit(200))
                 if (j.payload or {}).get("pass_id") == p.id][:20]
    page_url = f"{ctx(request)[0].public_base_url}/p/{p.download_token}"
    return page(request, user, session, t, "passes", "pass_detail", p=p, pass_jobs=pass_jobs, page_url=page_url)


@router.post("/passes/{pass_id}/void")
def pass_void(tenant_id: str, pass_id: str, request: Request, user: User = Depends(admin_user),
              session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    p = services.get_pass(session, t, pass_id)
    services.void_pass(session, p)
    audit(session, user, "admin.pass.void", t.id, p.id, serial=p.serial_number)
    session.commit()
    return back(f"/admin/tenants/{t.id}/passes/{p.id}", request, "Pass gesperrt.")


# ---------------------------------------------------------------- Team

@router.get("/team")
def team(tenant_id: str, request: Request, user: User = Depends(admin_user), session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    members = session.scalars(select(User).where(User.tenant_id == t.id).order_by(User.created_at)).all()
    invites = session.scalars(select(Invitation).where(Invitation.tenant_id == t.id)
                              .order_by(Invitation.expires_at.desc())).all()
    return page(request, user, session, t, "team", "team", members=members, invites=invites, now=utcnow())


# ---------------------------------------------------------------- Integrationen

@router.get("/integrations")
def integrations(tenant_id: str, request: Request, user: User = Depends(admin_user),
                 session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    keys = session.scalars(select(ApiKey).where(ApiKey.tenant_id == t.id).order_by(ApiKey.created_at.desc())).all()
    hooks = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == t.id)
                            .order_by(WebhookEndpoint.created_at)).all()
    return page(request, user, session, t, "integrations", "integrations", keys=keys, hooks=hooks)


@router.post("/keys/{key_id}/revoke")
def key_revoke(tenant_id: str, key_id: str, request: Request, user: User = Depends(admin_user),
               session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    k = _own(session.get(ApiKey, key_id), t, "Schlüssel")
    k.revoked_at = k.revoked_at or utcnow()
    audit(session, user, "admin.apikey.revoke", t.id, k.prefix)
    session.commit()
    return back(f"/admin/tenants/{t.id}/integrations", request, f"Schlüssel wk_{k.prefix}_… widerrufen.")


@router.post("/webhooks/{hook_id}/toggle")
def webhook_toggle(tenant_id: str, hook_id: str, request: Request, user: User = Depends(admin_user),
                   session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    ep = _own(session.get(WebhookEndpoint, hook_id), t, "Webhook")
    ep.active = not ep.active
    audit(session, user, "admin.webhook." + ("enable" if ep.active else "disable"), t.id, ep.id, url=ep.url)
    session.commit()
    return back(f"/admin/tenants/{t.id}/integrations", request,
                "Webhook eingeschaltet." if ep.active else "Webhook pausiert.")


# ---------------------------------------------------------------- Jobs und Aktivität

@router.get("/jobs")
def jobs(tenant_id: str, request: Request, status: str = "", user: User = Depends(admin_user),
         session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    stmt = select(Job).where(Job.tenant_id == t.id)
    if status:
        stmt = stmt.where(Job.status == status)
    rows = session.scalars(stmt.order_by(Job.updated_at.desc()).limit(100)).all()
    by_status = dict(session.execute(select(Job.status, func.count()).where(Job.tenant_id == t.id)
                                     .group_by(Job.status)).all())
    return page(request, user, session, t, "jobs", "jobs", rows=rows, status=status, by_status=by_status)


@router.post("/jobs/{job_id}/retry")
def job_retry(tenant_id: str, job_id: str, request: Request, user: User = Depends(admin_user),
              session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    job = _own(session.get(Job, job_id), t, "Job")
    job.status, job.attempts, job.run_after = "pending", 0, utcnow()
    audit(session, user, "admin.job.retry", t.id, job.id, kind=job.kind)
    session.commit()
    return back(f"/admin/tenants/{t.id}/jobs", request, "Job wird erneut ausgeführt.")


@router.get("/activity")
def activity(tenant_id: str, request: Request, page_no: int = 1, user: User = Depends(admin_user),
             session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    rows = session.scalars(select(AuditLog).where(AuditLog.tenant_id == t.id).order_by(AuditLog.created_at.desc())
                           .limit(101).offset((max(page_no, 1) - 1) * 100)).all()
    return page(request, user, session, t, "activity", "activity", rows=rows[:100], more=len(rows) > 100,
                page_no=max(page_no, 1))
