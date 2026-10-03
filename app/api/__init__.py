"""FastAPI-Anwendung: ``uvicorn app.api:create_app --factory``."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__
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


def create_app(settings=None, engine=None, signers=None):
    settings = settings or get_settings()
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

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    app.include_router(routes.router)
    app.include_router(public.router)
    app.include_router(apple.router)

    from .. import web
    web.install(app)
    return app
