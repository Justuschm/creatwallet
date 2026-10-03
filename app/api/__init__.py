"""FastAPI-Anwendung: ``uvicorn app.api:create_app --factory``."""

import hmac
import ipaddress
import time

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__, metrics
from ..certs import SignerProvider, make_store
from ..config import get_settings
from ..db import make_engine, make_sessionmaker
from ..security import Vault
from ..services import ServiceError, issues_json
from . import apple, public, routes

DESCRIPTION = """
API zum Ausgeben von Apple-Wallet-Pässen.

**Anmeldung:** Header `Authorization: Bearer wk_…` mit dem API-Schlüssel aus dem Portal.

**Ablauf:** Vorlage anlegen (`POST /api/v1/templates`), nach der Freigabe Pässe ausgeben
(`POST /api/v1/passes`) und dem Endkunden `page_url` zeigen oder schicken.
"""


def _internal(request):
    try:
        ip = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def create_app(settings=None, engine=None, signers=None):
    settings = settings or get_settings()
    metrics.init_error_reporting(settings.sentry_dsn, settings.environment, "api")
    engine = engine or make_engine(settings.database_url)
    vault = Vault(settings.secret_key)
    app = FastAPI(title="Wallet-Pass-API", version=__version__, description=DESCRIPTION,
                  docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.vault = vault
    app.state.signers = signers or SignerProvider(make_store(settings), settings.wwdr_path)

    @app.exception_handler(ServiceError)
    def service_error(request: Request, exc: ServiceError):
        if request.url.path.startswith(("/portal", "/admin")) and "json" not in request.headers.get("accept", ""):
            from ..web import html_error
            return html_error(request, exc)
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse({"error": exc.message, "issues": issues_json(exc.issues)}, status_code=exc.status,
                            headers=headers)

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, exc: RequestValidationError):
        issues = [{"level": "error", "path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                  for e in exc.errors()]
        return JSONResponse({"error": "Ungültige Anfrage.", "issues": issues}, status_code=422)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_request_bytes:
            return JSONResponse({"error": "Anfrage zu groß.", "issues": []}, status_code=413)
        return await call_next(request)

    @app.middleware("http")
    async def measure(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unbekannt")
        if path not in ("/metrics", "/healthz"):
            metrics.HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
            metrics.HTTP_SECONDS.labels(path).observe(time.perf_counter() - start)
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        """Bereit, wenn die Datenbank antwortet."""
        try:
            with app.state.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        except Exception:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": "Datenbank nicht erreichbar"}, status_code=503)
        return {"ok": True}

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint(request: Request):
        """Für Prometheus: aus dem internen Netz frei, von außen nur mit WALLET_METRICS_TOKEN."""
        token = request.app.state.settings.metrics_token
        sent = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not (_internal(request) or (token and hmac.compare_digest(sent, token))):
            return JSONResponse({"error": "Kein Zugriff."}, status_code=403)
        body, content_type = metrics.render_metrics(app.state.sessionmaker)
        return Response(body, media_type=content_type)

    app.include_router(routes.router)
    app.include_router(public.router)
    app.include_router(apple.router)

    from .. import web
    web.install(app)
    return app
