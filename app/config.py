"""Einstellungen aus Umgebungsvariablen (Präfix ``WALLET_``)."""

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name, default=None):
    """Wert aus ``WALLET_<NAME>`` oder aus der Datei in ``WALLET_<NAME>_FILE`` (Docker Secrets)."""
    path = os.environ.get(f"WALLET_{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(f"WALLET_{name}", default)


def _bool(value):
    return str(value).lower() in ("1", "true", "yes", "ja", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./wallet.db"
    # Öffentliche Adresse, unter der Download-Links erreichbar sind (ohne / am Ende).
    public_base_url: str = "http://localhost:8000"
    # Schlüssel zum Verschlüsseln gespeicherter Geheimnisse (Fernet, 32 Byte base64).
    secret_key: str = ""
    # Apple WWDR-Zwischenzertifikat (G4), für alle Pass Type IDs gleich.
    wwdr_path: str = ""
    # Ablage der Signier-Zertifikate: "file" (Verzeichnis) oder "openbao".
    cert_backend: str = "file"
    cert_dir: str = "./certs/store"
    openbao_addr: str = "http://127.0.0.1:8200"
    openbao_token: str = ""
    openbao_mount: str = "secret"
    openbao_prefix: str = "wallet/certs"
    # Neue Vorlagen müssen vom Admin freigegeben werden, bevor Pässe ausgegeben werden.
    require_template_approval: bool = True
    # Apple-Web-Service (Updates) in die Pässe eintragen.
    apple_web_service: bool = True
    # APNs: Wallet-Pässe nutzen immer den Produktions-Host.
    apns_host: str = "api.push.apple.com"
    # Optionaler Header apns-push-type (leer = nicht senden); beim ersten echten Test prüfen.
    apns_push_type: str = ""
    # Webhooks auch an http:// und interne Adressen (nur für lokale Tests!)
    allow_insecure_webhooks: bool = False
    # Login für Portal und Admin über Authentik (OpenID Connect)
    oidc_issuer: str = ""  # z. B. https://auth.example.de/application/o/wallet/
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    admin_group: str = "wallet-admins"
    # Admin-Bereich nur aus diesen Netzen (CIDR, kommagetrennt; leer = alle)
    admin_networks: tuple = field(default_factory=tuple)
    # NUR für Tests/lokale Entwicklung: Anmeldung ohne Authentik über /auth/dev-login
    dev_login: bool = False
    # Neue Firmen dürfen sich selbst registrieren (sonst nur per Einladung)
    allow_signup: bool = True
    max_request_bytes: int = 15 * 1024 * 1024
    cors_origins: tuple = field(default_factory=tuple)


@lru_cache
def get_settings():
    return Settings(
        database_url=_env("DATABASE_URL", Settings.database_url),
        public_base_url=_env("PUBLIC_BASE_URL", Settings.public_base_url).rstrip("/"),
        secret_key=_env("SECRET_KEY", ""),
        wwdr_path=_env("WWDR", ""),
        cert_backend=_env("CERT_BACKEND", Settings.cert_backend),
        cert_dir=_env("CERT_DIR", Settings.cert_dir),
        openbao_addr=_env("OPENBAO_ADDR", Settings.openbao_addr).rstrip("/"),
        openbao_token=_env("OPENBAO_TOKEN", ""),
        openbao_mount=_env("OPENBAO_MOUNT", Settings.openbao_mount),
        openbao_prefix=_env("OPENBAO_PREFIX", Settings.openbao_prefix),
        require_template_approval=_bool(_env("REQUIRE_TEMPLATE_APPROVAL", "true")),
        apple_web_service=_bool(_env("APPLE_WEB_SERVICE", "true")),
        apns_host=_env("APNS_HOST", Settings.apns_host),
        apns_push_type=_env("APNS_PUSH_TYPE", ""),
        allow_insecure_webhooks=_bool(_env("ALLOW_INSECURE_WEBHOOKS", "false")),
        oidc_issuer=_env("OIDC_ISSUER", ""),
        oidc_client_id=_env("OIDC_CLIENT_ID", ""),
        oidc_client_secret=_env("OIDC_CLIENT_SECRET", ""),
        admin_group=_env("ADMIN_GROUP", Settings.admin_group),
        admin_networks=tuple(n.strip() for n in (_env("ADMIN_NETWORKS", "") or "").split(",") if n.strip()),
        allow_signup=_bool(_env("ALLOW_SIGNUP", "true")),
        dev_login=_bool(_env("DEV_LOGIN", "false")),
        cors_origins=tuple(o for o in (_env("CORS_ORIGINS", "") or "").split(",") if o),
    )
