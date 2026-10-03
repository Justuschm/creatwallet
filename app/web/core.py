"""Gemeinsames für Portal und Admin: Sitzung, angemeldeter Benutzer, Rechte, CSRF, Meldungen."""

import hmac
import ipaddress
import secrets
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..api.deps import get_session
from ..placeholders import find as find_placeholders
from ..models import AuditLog, User

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["placeholders"] = find_placeholders
templates.env.filters["dt"] = lambda d: d.strftime("%d.%m.%Y %H:%M") if d else "–"
templates.env.filters["d"] = lambda d: d.strftime("%d.%m.%Y") if d else "–"

ROLES = {
    "owner": "Inhaber",
    "admin": "Admin",
    "designer": "Gestalter",
    "issuer": "Ausgabe",
    "viewer": "Nur lesen",
}
PERMISSIONS = {
    "owner": {"templates", "passes", "team", "keys", "settings"},
    "admin": {"templates", "passes", "team", "keys", "settings"},
    "designer": {"templates"},
    "issuer": {"passes"},
    "viewer": set(),
}


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, message="Dafür fehlen dir die Rechte."):
        super().__init__(message)
        self.message = message


class Redirect(Exception):
    def __init__(self, url):
        super().__init__(url)
        self.url = url


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    uid = request.session.get("uid")
    user = session.get(User, uid) if uid else None
    if user is None:
        raise LoginRequired()
    return user


def portal_user(request: Request, user: User = Depends(current_user)) -> User:
    """Benutzer mit Firma; ohne Firma geht es zur Einrichtung."""
    if user.tenant_id is None:
        raise Redirect("/portal/onboarding")
    return user


def can(user, permission):
    return permission in PERMISSIONS.get(user.role, set())


def require(user, permission):
    if not can(user, permission):
        raise Forbidden()


def admin_user(request: Request, user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise Forbidden("Nur für Plattform-Admins.")
    networks = request.app.state.settings.admin_networks
    if networks:
        try:
            client = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError:
            client = None
        if client is None or not any(client in ipaddress.ip_network(n, strict=False) for n in networks):
            raise Forbidden("Der Admin-Bereich ist von hier aus nicht erreichbar.")
    return user


def csrf_token(request: Request):
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


async def check_csrf(request: Request):
    """Für alle POST-Formulare: verstecktes Feld ``csrf`` muss zur Sitzung passen."""
    if request.method != "POST":
        return
    form = await request.form()
    sent = form.get("csrf") or request.headers.get("x-csrf-token", "")
    if not sent or not hmac.compare_digest(str(sent), request.session.get("csrf", "")):
        raise Forbidden("Sitzung abgelaufen oder ungültiges Formular - bitte Seite neu laden.")


def flash(request: Request, message, kind="ok"):
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, name, user=None, status_code=200, **ctx):
    messages = request.session.pop("flash", [])
    return templates.TemplateResponse(request, name, {
        "user": user, "csrf": csrf_token(request), "messages": messages, "ROLES": ROLES,
        "can": (lambda p: can(user, p)) if user else (lambda p: False), **ctx}, status_code=status_code)


def audit(session, user, action, tenant_id=None, object_id="", **details):
    actor = (user.email or user.oidc_sub) if user else "system"
    session.add(AuditLog(actor=actor, action=action,
                         tenant_id=tenant_id, object_id=str(object_id or ""), details=details))
