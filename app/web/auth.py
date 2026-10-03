"""Anmeldung über Authentik (OpenID Connect), Einladungen und Einrichtung neuer Firmen."""

from datetime import timezone

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import get_session
from ..models import Invitation, Tenant, User, utcnow
from .core import Forbidden, audit, check_csrf, current_user, flash, render

router = APIRouter(include_in_schema=False)


def setup_oauth(settings):
    if not settings.oidc_issuer:
        return None
    oauth = OAuth()
    oauth.register(
        name="authentik",
        server_metadata_url=settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    return oauth.authentik


def _safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") else ""


def _landing(user):
    if user.tenant_id:
        return "/portal"
    return "/admin" if user.is_admin else "/portal/onboarding"


def _sign_in(request, session, sub, email, name, groups):
    settings = request.app.state.settings
    user = session.scalars(select(User).where(User.oidc_sub == sub)).one_or_none()
    if user is None:
        user = User(oidc_sub=sub)
        session.add(user)
    user.email = email or user.email
    user.name = name or user.name
    user.is_admin = settings.admin_group in (groups or [])
    user.last_login_at = utcnow()
    session.flush()
    token = request.session.pop("invite", None)
    if token:
        _accept_invitation(request, session, user, token)
    session.commit()
    messages = request.session.get("flash", [])
    request.session.clear()  # neue Sitzung nach dem Login (Schutz vor Session Fixation)
    request.session["uid"] = user.id
    if messages:
        request.session["flash"] = messages
    return user


def _accept_invitation(request, session, user, token):
    inv = session.scalars(select(Invitation).where(Invitation.token == token)).one_or_none()
    expires = inv.expires_at if inv else None
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if inv is None or inv.accepted_at is not None or expires < utcnow():
        flash(request, "Die Einladung ist ungültig oder abgelaufen.", "error")
        return
    if user.tenant_id and user.tenant_id != inv.tenant_id:
        flash(request, "Dein Zugang gehört bereits zu einer anderen Firma.", "error")
        return
    user.tenant_id = inv.tenant_id
    user.role = inv.role
    inv.accepted_at = utcnow()
    audit(session, user, "team.join", inv.tenant_id, user.id, role=inv.role)
    flash(request, f"Willkommen bei {inv.tenant.name}!")


@router.get("/auth/login")
async def login(request: Request, next: str = ""):
    request.session["next"] = _safe_next(next)
    client = request.app.state.oauth
    if client is None:
        if request.app.state.settings.dev_login:
            return RedirectResponse("/auth/dev-login", status_code=303)
        return render(request, "message.html", title="Login nicht eingerichtet",
                      text="WALLET_OIDC_ISSUER, …_CLIENT_ID und …_CLIENT_SECRET setzen (siehe README).",
                      status_code=503)
    redirect_uri = request.app.state.settings.public_base_url + "/auth/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def callback(request: Request, session: Session = Depends(get_session)):
    client = request.app.state.oauth
    if client is None:
        raise Forbidden("Login nicht eingerichtet.")
    try:
        token = await client.authorize_access_token(request)
    except OAuthError as exc:
        return render(request, "message.html", title="Anmeldung fehlgeschlagen", text=str(exc.description or exc),
                      status_code=400)
    info = token.get("userinfo") or await client.userinfo(token=token)
    nxt = request.session.get("next", "")
    user = _sign_in(request, session, info["sub"], info.get("email", ""), info.get("name", ""),
                    info.get("groups", []))
    request.session["id_token"] = token.get("id_token", "")
    return RedirectResponse(nxt or _landing(user), status_code=303)


@router.get("/auth/dev-login")
def dev_login_form(request: Request):
    if not request.app.state.settings.dev_login:
        raise Forbidden("Nicht verfügbar.")
    return render(request, "dev_login.html")


@router.post("/auth/dev-login", dependencies=[Depends(check_csrf)])
def dev_login(request: Request, email: str = Form(...), admin: bool = Form(False),
              session: Session = Depends(get_session)):
    """Nur für Tests und lokale Entwicklung (WALLET_DEV_LOGIN=true) - nie in Produktion einschalten."""
    if not request.app.state.settings.dev_login:
        raise Forbidden("Nicht verfügbar.")
    nxt = request.session.get("next", "")
    user = _sign_in(request, session, "dev:" + email, email, email.split("@")[0],
                    [request.app.state.settings.admin_group] if admin else [])
    return RedirectResponse(nxt or _landing(user), status_code=303)


@router.get("/auth/logout")
async def logout(request: Request):
    id_token = request.session.get("id_token", "")
    request.session.clear()
    client = request.app.state.oauth
    if client is not None:
        meta = await client.load_server_metadata()
        end = meta.get("end_session_endpoint")
        if end:
            return RedirectResponse(f"{end}?id_token_hint={id_token}", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/invite/{token}")
def invite(token: str, request: Request, session: Session = Depends(get_session)):
    uid = request.session.get("uid")
    user = session.get(User, uid) if uid else None
    if user is None:
        request.session["invite"] = token
        return RedirectResponse("/auth/login", status_code=303)
    _accept_invitation(request, session, user, token)
    session.commit()
    return RedirectResponse(_landing(user), status_code=303)


@router.get("/portal/onboarding")
def onboarding_form(request: Request, user: User = Depends(current_user)):
    if user.tenant_id:
        return RedirectResponse("/portal", status_code=303)
    return render(request, "onboarding.html", user, signup=request.app.state.settings.allow_signup)


@router.post("/portal/onboarding", dependencies=[Depends(check_csrf)])
def onboarding(request: Request, name: str = Form(...), organization_name: str = Form(""), address: str = Form(""),
               vat_id: str = Form(""), terms: bool = Form(False), user: User = Depends(current_user),
               session: Session = Depends(get_session)):
    if user.tenant_id:
        return RedirectResponse("/portal", status_code=303)
    if not request.app.state.settings.allow_signup:
        raise Forbidden("Neue Firmen werden nur auf Einladung angelegt.")
    name = name.strip()[:200]
    if not name or not terms:
        flash(request, "Bitte Firmennamen angeben und AGB sowie Auftragsverarbeitung akzeptieren.", "error")
        return RedirectResponse("/portal/onboarding", status_code=303)
    tenant = Tenant(name=name, organization_name=(organization_name.strip() or name)[:200], status="pending",
                    address=address.strip()[:1000], vat_id=vat_id.strip()[:40], contact_email=user.email)
    session.add(tenant)
    session.flush()
    user.tenant_id, user.role = tenant.id, "owner"
    audit(session, user, "tenant.signup", tenant.id, tenant.id, name=name)
    session.commit()
    flash(request, "Firma angelegt. Wir prüfen die Angaben und schalten das Konto frei - Vorlagen kannst du "
                   "schon jetzt gestalten.")
    return RedirectResponse("/portal", status_code=303)
