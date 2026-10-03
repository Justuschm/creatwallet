"""Weboberflächen: Kunden-Portal (/portal) und Admin-Bereich (/admin) mit Login über Authentik."""

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from ..services import ServiceError
from . import admin, auth, portal
from .core import Forbidden, LoginRequired, Redirect, render


def _session_secret(settings):
    # Eigener Schlüssel für Sitzungs-Cookies, abgeleitet aus WALLET_SECRET_KEY.
    return hmac.new(settings.secret_key.encode(), b"session-cookie", hashlib.sha256).hexdigest()


def install(app):
    settings = app.state.settings
    app.state.oauth = auth.setup_oauth(settings)
    app.add_middleware(SessionMiddleware, secret_key=_session_secret(settings), session_cookie="wallet_session",
                       max_age=12 * 3600, same_site="lax", https_only=settings.public_base_url.startswith("https"))

    @app.exception_handler(LoginRequired)
    def login_required(request: Request, exc: LoginRequired):
        return RedirectResponse(f"/auth/login?next={request.url.path}", status_code=303)

    @app.exception_handler(Redirect)
    def redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(Forbidden)
    def forbidden(request: Request, exc: Forbidden):
        return render(request, "message.html", title="Kein Zugriff", text=exc.message, status_code=403)

    @app.get("/", include_in_schema=False)
    def home():
        return RedirectResponse("/portal", status_code=303)

    app.include_router(auth.router)
    app.include_router(portal.router)
    app.include_router(admin.router)


def html_error(request: Request, exc: ServiceError):
    """ServiceError in Portal/Admin als Seite statt JSON."""
    return render(request, "message.html", title="Das hat nicht geklappt", text=exc.message,
                  status_code=exc.status)
