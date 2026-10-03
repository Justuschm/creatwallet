"""Hintergrundaufgaben anlegen. Ausgeführt werden sie vom Worker (``python -m app worker``)."""

from datetime import timedelta

from sqlalchemy import func, select

from .models import Job, Registration, WebhookEndpoint, new_id, utcnow

PUSH = "push"
WEBHOOK = "webhook"

# Ereignisse, die Firmen per Webhook abonnieren können
EVENTS = ("pass.installed", "pass.removed", "pass.updated", "pass.voided")


def enqueue(session, kind, payload, delay_seconds=0, tenant_id=None):
    job = Job(kind=kind, payload=payload, tenant_id=tenant_id,
              run_after=utcnow() + timedelta(seconds=delay_seconds))
    session.add(job)
    return job


def enqueue_push(session, pass_obj, device_ids=None):
    """Push an alle Geräte, auf denen der Pass liegt (sofern es welche gibt)."""
    if device_ids is None:
        count = session.scalar(select(func.count(Registration.id)).where(Registration.pass_id == pass_obj.id))
        if not count:
            return None
    return enqueue(session, PUSH, {"pass_id": pass_obj.id, "device_ids": device_ids}, tenant_id=pass_obj.tenant_id)


def emit(session, tenant_id, event, data):
    """Ereignis an alle passenden Webhook-Endpunkte der Firma verschicken."""
    endpoints = session.scalars(select(WebhookEndpoint).where(WebhookEndpoint.tenant_id == tenant_id,
                                                              WebhookEndpoint.active.is_(True))).all()
    event_id = "evt_" + new_id().replace("-", "")
    created = utcnow().isoformat(timespec="seconds")
    for ep in endpoints:
        if event in (ep.events or []):
            enqueue(session, WEBHOOK, {"endpoint_id": ep.id, "body": {
                "id": event_id, "type": event, "created_at": created, "data": data}}, tenant_id=tenant_id)


def pass_event_data(pass_obj):
    return {"pass_id": pass_obj.id, "serial_number": pass_obj.serial_number,
            "template_id": pass_obj.template_id, "version": pass_obj.version, "status": pass_obj.status}
