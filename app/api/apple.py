"""Apple-Web-Service für Wallet-Pässe (von Apple vorgegebene Pfade unter ``/v1``).

iPhones melden sich hier an, wenn ein Pass hinzugefügt wird, fragen nach einem Push,
welche Pässe sich geändert haben, und laden die neue Version. Die Anmeldung erfolgt mit
dem ``authenticationToken`` des Passes: ``Authorization: ApplePass <token>``.
"""

import hmac
import logging
from datetime import timezone
from email.utils import format_datetime, parsedate_to_datetime

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import jobs, metrics, services
from ..models import Certificate, Device, Pass, Registration, Tenant, utcnow
from .deps import ctx, get_session

router = APIRouter(prefix="/v1", include_in_schema=False)
log = logging.getLogger("wallet.apple")
PKPASS = "application/vnd.apple.pkpass"


def _aware(dt):
    """Immer UTC mit datetime.timezone.utc (SQLite liefert naive Zeiten, PostgreSQL eigene Zeitzonen-Objekte)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _tag(dt):
    return str(int(_aware(dt).timestamp() * 1_000_000))


def _find_pass(session, pti, serial):
    return session.scalars(
        select(Pass).join(Tenant, Pass.tenant_id == Tenant.id).join(Certificate, Tenant.certificate_id == Certificate.id)
        .where(Certificate.pass_type_identifier == pti, Pass.serial_number == serial)).first()


def _authorized_pass(request, session, pti, serial):
    """Pass, wenn der ApplePass-Token stimmt, sonst None (Antwort 401)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme != "ApplePass" or not token:
        return None
    p = _find_pass(session, pti, serial)
    if p is None:
        return None
    expected = request.app.state.vault.decrypt(p.auth_token_enc)
    return p if hmac.compare_digest(token.strip(), expected) else None


@router.post("/devices/{device_id}/registrations/{pti}/{serial}")
def register(device_id: str, pti: str, serial: str, request: Request, body: dict = Body(default_factory=dict),
             session: Session = Depends(get_session)):
    p = _authorized_pass(request, session, pti, serial)
    if p is None:
        return Response(status_code=401)
    push_token = str(body.get("pushToken", ""))[:200]
    if not push_token or len(device_id) > 200:
        return Response(status_code=400)
    device = session.scalars(select(Device).where(Device.device_library_identifier == device_id)).one_or_none()
    if device is None:
        device = Device(device_library_identifier=device_id, push_token=push_token)
        session.add(device)
        session.flush()
    elif device.push_token != push_token:
        device.push_token = push_token
        device.updated_at = utcnow()
    existing = session.scalars(select(Registration).where(Registration.device_id == device.id,
                                                          Registration.pass_id == p.id)).one_or_none()
    if existing is not None:
        session.commit()
        return Response(status_code=200)
    session.add(Registration(device_id=device.id, pass_id=p.id))
    metrics.DEVICE_REGISTRATIONS.labels("register").inc()
    jobs.emit(session, p.tenant_id, "pass.installed", jobs.pass_event_data(p))
    session.commit()
    return Response(status_code=201)


@router.delete("/devices/{device_id}/registrations/{pti}/{serial}")
def unregister(device_id: str, pti: str, serial: str, request: Request, session: Session = Depends(get_session)):
    p = _authorized_pass(request, session, pti, serial)
    if p is None:
        return Response(status_code=401)
    reg = session.scalars(select(Registration).join(Device).where(
        Device.device_library_identifier == device_id, Registration.pass_id == p.id)).one_or_none()
    if reg is not None:
        device_pk = reg.device_id
        session.delete(reg)
        session.flush()
        if not session.scalar(select(func.count(Registration.id)).where(Registration.device_id == device_pk)):
            session.execute(delete(Device).where(Device.id == device_pk))
        jobs.emit(session, p.tenant_id, "pass.removed", jobs.pass_event_data(p))
        metrics.DEVICE_REGISTRATIONS.labels("unregister").inc()
        session.commit()
    return Response(status_code=200)


@router.get("/devices/{device_id}/registrations/{pti}")
def updated_serials(device_id: str, pti: str, passesUpdatedSince: str | None = None,  # noqa: N803 - Apples Name
                    session: Session = Depends(get_session)):
    q = (select(Pass).join(Registration, Registration.pass_id == Pass.id).join(Device)
         .join(Tenant, Pass.tenant_id == Tenant.id).join(Certificate, Tenant.certificate_id == Certificate.id)
         .where(Device.device_library_identifier == device_id, Certificate.pass_type_identifier == pti))
    passes = session.scalars(q).all()
    if passesUpdatedSince and passesUpdatedSince.isdigit():
        since = int(passesUpdatedSince)
        passes = [p for p in passes if int(_tag(p.updated_at)) > since]
    if not passes:
        return Response(status_code=204)
    last = max(p.updated_at for p in passes)
    return {"serialNumbers": [p.serial_number for p in passes], "lastUpdated": _tag(last)}


@router.get("/passes/{pti}/{serial}")
def latest_pass(pti: str, serial: str, request: Request, session: Session = Depends(get_session)):
    p = _authorized_pass(request, session, pti, serial)
    if p is None:
        return Response(status_code=401)
    modified = _aware(p.updated_at).replace(microsecond=0)
    since = request.headers.get("if-modified-since")
    if since:
        try:
            if modified <= _aware(parsedate_to_datetime(since)):
                return Response(status_code=304)
        except (TypeError, ValueError):
            pass
    settings, signers, vault = ctx(request)
    data = services.build_pass(p, settings, signers, vault, source="apple")
    return Response(data, media_type=PKPASS, headers={"Last-Modified": format_datetime(modified, usegmt=True),
                                                      "Cache-Control": "no-cache"})


@router.post("/log")
def device_log(body: dict = Body(default_factory=dict)):
    for line in (body.get("logs") or [])[:50]:
        log.warning("Wallet-Gerät meldet: %s", str(line)[:500])
    return Response(status_code=200)
