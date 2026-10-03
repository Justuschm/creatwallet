"""Gemeinsame Abhängigkeiten der Routen: Datenbank-Session, Anmeldung per API-Schlüssel."""

from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ApiKey, Tenant, utcnow
from ..security import secret_matches, split_api_key
from ..services import ServiceError


def get_session(request: Request):
    session = request.app.state.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_tenant(request: Request, session: Session = Depends(get_session)) -> Tenant:
    header = request.headers.get("authorization", "")
    scheme, _, key = header.partition(" ")
    parts = split_api_key(key.strip()) if scheme.lower() == "bearer" else None
    if not parts:
        raise ServiceError(401, "API-Schlüssel fehlt. Header: Authorization: Bearer wk_…")
    prefix, secret = parts
    api_key = session.scalars(select(ApiKey).where(ApiKey.prefix == prefix)).one_or_none()
    if api_key is None or api_key.revoked_at is not None or not secret_matches(secret, api_key.secret_hash):
        raise ServiceError(401, "API-Schlüssel ungültig oder widerrufen.")
    now = utcnow()
    last = api_key.last_used_at
    if last is not None and last.tzinfo is None:  # SQLite liefert Zeiten ohne Zeitzone
        last = last.replace(tzinfo=now.tzinfo)
    if last is None or now - last > timedelta(minutes=5):
        api_key.last_used_at = now
        session.commit()
    return api_key.tenant


def ctx(request: Request):
    """Settings, Signer und Vault aus dem App-Zustand."""
    s = request.app.state
    return s.settings, s.signers, s.vault
