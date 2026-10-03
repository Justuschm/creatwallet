"""Kunden-API ``/api/v1``: Vorlagen und Pässe."""

import base64
import binascii
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from creatwallet.templates import TEMPLATES, new_pass, placeholder_images

from .. import placeholders, services
from ..models import Pass, Template, Tenant, WebhookEndpoint
from ..services import ServiceError, issues_json
from .deps import ctx, get_session, get_tenant
from .schemas import (AccountOut, PassCreate, PassList, PassOut, PassUpdate, TemplateCreate, TemplateOut,
                      TemplateUpdate, TestPassRequest, WebhookCreate, WebhookOut)

router = APIRouter(prefix="/api/v1")
PKPASS = "application/vnd.apple.pkpass"


def _decode_images(images):
    if images is None:
        return None
    out = {}
    for name, b64 in images.items():
        try:
            out[name] = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ServiceError(422, f"{name}: kein gültiges base64.") from exc
    return out


def template_out(t: Template, *, full=False, issues=None):
    latest = t.latest_version
    return TemplateOut(
        id=t.id, name=t.name,
        status="approved" if t.approved_version else latest.status,
        approved_version=t.approved_version.number if t.approved_version else None,
        latest_version={"number": latest.number, "status": latest.status, "review_note": latest.review_note,
                        "created_at": latest.created_at},
        placeholders=placeholders.find(latest.pass_json),
        images=[f.name for f in latest.files],
        created_at=t.created_at, updated_at=t.updated_at,
        pass_json=latest.pass_json if full else None,
        issues=issues_json(issues) if issues is not None else None,
    )


def pass_out(p: Pass, settings):
    base = f"{settings.public_base_url}/p/{p.download_token}"
    return PassOut(id=p.id, serial_number=p.serial_number, template_id=p.template_id, status=p.status,
                   version=p.version, data=p.data, created_at=p.created_at, updated_at=p.updated_at,
                   installed_devices=len(p.registrations), page_url=base, download_url=f"{base}/pass.pkpass")


# ---------------------------------------------------------------- Konto

@router.get("/account", response_model=AccountOut, tags=["Konto"], summary="Eigenes Konto")
def account(tenant: Tenant = Depends(get_tenant)):
    cert = tenant.certificate
    return AccountOut(id=tenant.id, name=tenant.name, organization_name=tenant.organization_name,
                      status=tenant.status, plan=tenant.plan,
                      pass_type_identifier=cert.pass_type_identifier if cert else None,
                      certificate_expires_at=cert.expires_at if cert else None)


# ---------------------------------------------------------------- Vorlagen

@router.get("/templates", response_model=list[TemplateOut], response_model_exclude_none=True,
            tags=["Vorlagen"], summary="Vorlagen auflisten")
def list_templates(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    return [template_out(t) for t in services.list_templates(session, tenant)]


@router.post("/templates", response_model=TemplateOut, response_model_exclude_none=True, status_code=201,
             tags=["Vorlagen"], summary="Vorlage anlegen")
def create_template(body: TemplateCreate, request: Request, tenant: Tenant = Depends(get_tenant),
                    session: Session = Depends(get_session)):
    settings, _, _ = ctx(request)
    pass_json, images = body.pass_json, _decode_images(body.images)
    if body.base_template:
        if body.base_template not in TEMPLATES:
            raise ServiceError(422, "Unbekannte base_template. Möglich: " + ", ".join(TEMPLATES))
        pass_json = pass_json if pass_json is not None else new_pass(body.base_template)
        images = images if images is not None else placeholder_images(body.base_template, scales=(2, 3))
    if pass_json is None:
        raise ServiceError(422, "pass oder base_template angeben.")
    template, version = services.create_template(session, tenant, body.name, pass_json, images, settings)
    issues = services.preview_issues(tenant, version.pass_json, version.file_map())
    session.commit()
    return template_out(template, full=True, issues=issues)


@router.get("/templates/{template_id}", response_model=TemplateOut, response_model_exclude_none=True,
            tags=["Vorlagen"], summary="Vorlage abrufen")
def get_template(template_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    t = services.get_template(session, tenant, template_id)
    return template_out(t, full=True)


@router.patch("/templates/{template_id}", response_model=TemplateOut, response_model_exclude_none=True,
              tags=["Vorlagen"], summary="Vorlage ändern (neue Version)",
              description="Eine geänderte pass.json oder neue Bilder erzeugen eine neue Version. Bis zur "
                          "Freigabe werden Pässe weiter aus der zuletzt freigegebenen Version gebaut.")
def update_template(template_id: str, body: TemplateUpdate, request: Request,
                    tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    settings, _, _ = ctx(request)
    t = services.get_template(session, tenant, template_id)
    _, version = services.update_template(session, t, body.name, body.pass_json, _decode_images(body.images),
                                          settings)
    issues = services.preview_issues(tenant, version.pass_json, version.file_map()) if version else None
    session.commit()
    return template_out(t, full=True, issues=issues)


@router.post("/templates/{template_id}/test-pass", tags=["Vorlagen"], summary="Test-Pass fürs eigene iPhone",
             response_class=Response, responses={200: {"content": {PKPASS: {}}}},
             description="Baut die neueste Version (auch vor der Freigabe) mit Beispielwerten. "
                         "Der Test-Pass läuft nach 24 Stunden ab.")
def test_pass(template_id: str, request: Request, body: TestPassRequest | None = None,
              tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    _, signers, _ = ctx(request)
    t = services.get_template(session, tenant, template_id)
    pkpass = services.build_test_pass(tenant, t.latest_version, body.data if body else None, signers)
    return Response(pkpass, media_type=PKPASS, headers={"Content-Disposition": 'attachment; filename="test.pkpass"'})


# ---------------------------------------------------------------- Pässe

@router.post("/passes", response_model=PassOut, status_code=201, tags=["Pässe"], summary="Pass ausgeben",
             description="Erzeugt einen Pass aus einer freigegebenen Vorlage. Mit dem Header Idempotency-Key "
                         "liefert eine Wiederholung denselben Pass (Status 200) statt eines zweiten.")
def create_pass(body: PassCreate, request: Request, response: Response,
                idempotency_key: str | None = Header(None),
                tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    settings, signers, vault = ctx(request)
    p, created = services.create_pass(session, tenant, body.template_id, body.data, settings, signers, vault,
                                      serial_number=body.serial_number, idempotency_key=idempotency_key)
    session.commit()
    if not created:
        response.status_code = 200
    return pass_out(p, settings)


@router.get("/passes", response_model=PassList, tags=["Pässe"], summary="Pässe auflisten (neueste zuerst)")
def list_passes(request: Request, template_id: str | None = None, limit: int = Query(50, ge=1, le=500),
                offset: int = Query(0, ge=0), tenant: Tenant = Depends(get_tenant),
                session: Session = Depends(get_session)):
    settings, _, _ = ctx(request)
    items = services.list_passes(session, tenant, template_id, limit, offset)
    return PassList(items=[pass_out(p, settings) for p in items], limit=limit, offset=offset)


@router.get("/passes/{pass_id}", response_model=PassOut, tags=["Pässe"], summary="Pass abrufen")
def get_pass(pass_id: str, request: Request, tenant: Tenant = Depends(get_tenant),
             session: Session = Depends(get_session)):
    return pass_out(services.get_pass(session, tenant, pass_id), ctx(request)[0])


@router.patch("/passes/{pass_id}", response_model=PassOut, tags=["Pässe"], summary="Pass ändern",
              description="Übernimmt die genannten Felder, erhöht die Version. Automatische Updates auf den "
                          "iPhones folgen in Phase 2.")
def update_pass(pass_id: str, body: PassUpdate, request: Request, tenant: Tenant = Depends(get_tenant),
                session: Session = Depends(get_session)):
    settings, signers, vault = ctx(request)
    p = services.get_pass(session, tenant, pass_id)
    services.update_pass(session, p, body.data, settings, signers, vault)
    session.commit()
    return pass_out(p, settings)


@router.post("/passes/{pass_id}/void", response_model=PassOut, tags=["Pässe"], summary="Pass sperren",
             description="Markiert den Pass als ungültig (z. B. storniertes Ticket). Nicht umkehrbar.")
def void_pass(pass_id: str, request: Request, tenant: Tenant = Depends(get_tenant),
              session: Session = Depends(get_session)):
    p = services.get_pass(session, tenant, pass_id)
    services.void_pass(session, p)
    session.commit()
    return pass_out(p, ctx(request)[0])


@router.get("/passes/{pass_id}/pkpass", tags=["Pässe"], summary="Pass-Datei herunterladen",
            response_class=Response, responses={200: {"content": {PKPASS: {}}}})
def download_pass(pass_id: str, request: Request, tenant: Tenant = Depends(get_tenant),
                  session: Session = Depends(get_session)):
    settings, signers, vault = ctx(request)
    p = services.get_pass(session, tenant, pass_id)
    data = services.build_pass(p, settings, signers, vault)
    return Response(data, media_type=PKPASS,
                    headers={"Content-Disposition": f'attachment; filename="{p.serial_number}.pkpass"'})


# ---------------------------------------------------------------- Webhooks

def webhook_out(ep, secret=None):
    return WebhookOut(id=ep.id, url=ep.url, events=ep.events, active=ep.active, created_at=ep.created_at,
                      secret=secret)


@router.get("/webhooks", response_model=list[WebhookOut], response_model_exclude_none=True, tags=["Webhooks"],
            summary="Webhooks auflisten")
def list_webhooks(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    return [webhook_out(ep) for ep in session.query(WebhookEndpoint).filter_by(tenant_id=tenant.id)
            .order_by(WebhookEndpoint.created_at)]


@router.post("/webhooks", response_model=WebhookOut, response_model_exclude_none=True, status_code=201,
             tags=["Webhooks"], summary="Webhook anlegen",
             description="Jede Zustellung trägt den Header Wallet-Signature: t=<unix>,v1=<hmac>. v1 ist "
                         "HMAC-SHA256 über '<t>.<body>' mit dem secret aus der Antwort.")
def create_webhook(body: WebhookCreate, request: Request, tenant: Tenant = Depends(get_tenant),
                   session: Session = Depends(get_session)):
    settings, _, vault = ctx(request)
    ep, secret = services.create_webhook(session, tenant, body.url, body.events, settings, vault)
    session.commit()
    return webhook_out(ep, secret)


@router.delete("/webhooks/{webhook_id}", status_code=204, tags=["Webhooks"], summary="Webhook löschen")
def delete_webhook(webhook_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    ep = session.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.tenant_id != tenant.id:
        raise ServiceError(404, "Webhook nicht gefunden.")
    session.delete(ep)
    session.commit()
    return Response(status_code=204)


@router.post("/webhooks/{webhook_id}/test", status_code=202, tags=["Webhooks"],
             summary="Test-Ereignis senden", description="Stellt ein Ereignis vom Typ webhook.test zu.")
def test_webhook(webhook_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
    ep = session.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.tenant_id != tenant.id:
        raise ServiceError(404, "Webhook nicht gefunden.")
    services.send_test_webhook(session, ep)
    session.commit()
    return {"queued": True}
