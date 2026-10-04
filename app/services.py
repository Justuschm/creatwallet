"""Geschäftslogik: Vorlagen und Pässe. Unabhängig vom Web-Framework, damit API, Portal
und Kommandozeile dieselben Regeln nutzen."""

import copy
import re

from sqlalchemy import func, select
from sqlalchemy.orm import object_session

from creatwallet import validation as v
from creatwallet.build import BuildError, build_pkpass

from . import jobs, metrics, placeholders
from .certs import CertStoreError
from .models import Pass, Template, TemplateFile, TemplateVersion, Tenant, utcnow
from datetime import timedelta

from . import webhooks
from .models import ApiKey, WebhookEndpoint
from .security import Vault, generate_api_key, random_token

# Diese Felder setzt die Plattform selbst; in Vorlagen werden sie ignoriert.
PLATFORM_KEYS = ("formatVersion", "serialNumber", "passTypeIdentifier", "teamIdentifier",
                 "authenticationToken", "webServiceURL")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
IMAGE_NAME_RE = re.compile(r"^([A-Za-z]{2,3}(-[A-Za-z0-9]+)?\.lproj/)?[A-Za-z]+(@[23]x)?\.png$")
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 40


class ServiceError(Exception):
    def __init__(self, status, message, issues=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.issues = issues or []


def issues_json(issues):
    return [{"level": i.level, "path": i.path, "message": i.message} for i in issues]


# ---------------------------------------------------------------- Vorlagen

def clean_template_input(pass_json, images):
    if not isinstance(pass_json, dict):
        raise ServiceError(422, "pass muss ein JSON-Objekt sein.")
    cleaned = {k: val for k, val in copy.deepcopy(pass_json).items() if k not in PLATFORM_KEYS}
    if images is not None:
        if len(images) > MAX_IMAGES:
            raise ServiceError(422, f"Höchstens {MAX_IMAGES} Bilder pro Vorlage.")
        for name, data in images.items():
            if not IMAGE_NAME_RE.match(name):
                raise ServiceError(422, f"Ungültiger Bildname: {name} (erwartet z. B. icon@2x.png).")
            if len(data) > MAX_IMAGE_BYTES:
                raise ServiceError(422, f"{name} ist größer als 2 MB.")
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ServiceError(422, f"{name} ist keine PNG-Datei.")
    return cleaned


def preview_issues(tenant, pass_json, files):
    """Prüfung einer Vorlage mit Beispielwerten statt der Platzhalter."""
    sample = placeholders.render(pass_json, placeholders.sample_data(placeholders.find(pass_json)))
    sample = platform_fields(sample, tenant, serial="beispiel", auth_token="x" * 16, settings=None, voided=False)
    issues = v.validate(sample, files)
    names = placeholders.find(pass_json)
    # Fehler, die nur wegen der Beispieltexte in Platzhalter-Feldern entstehen, als Hinweis zeigen.
    for i in issues:
        if i.level == v.ERROR and names and any(f"Beispiel {n}" in str(_lookup(sample, i.path)) for n in names):
            i.level = v.INFO
            i.message += " (Platzhalter - wird bei der Ausgabe mit echten Daten geprüft)"
    return issues


def _lookup(obj, path):
    cur = obj
    for part in re.findall(r"[^.\[\]]+", path or ""):
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def create_template(session, tenant, name, pass_json, images, settings):
    if not name or len(name) > 200:
        raise ServiceError(422, "name fehlt oder ist zu lang (max. 200 Zeichen).")
    cleaned = clean_template_input(pass_json, images or {})
    template = Template(tenant_id=tenant.id, name=name)
    session.add(template)
    version = _add_version(session, template, cleaned, images or {}, settings)
    return template, version


def update_template(session, template, name, pass_json, images, settings):
    if name:
        template.name = name
    if pass_json is None and images is None:
        return template, None
    base = template.latest_version
    cleaned = clean_template_input(pass_json if pass_json is not None else base.pass_json, images)
    files = images if images is not None else base.file_map()
    version = _add_version(session, template, cleaned, files, settings)
    return template, version


def _add_version(session, template, pass_json, files, settings):
    number = (template.latest_version.number + 1) if template.versions else 1
    version = TemplateVersion(template=template, number=number, pass_json=pass_json,
                              files=[TemplateFile(name=n, data=d) for n, d in sorted(files.items())])
    session.add(version)
    session.flush()
    if not settings.require_template_approval:
        approve_version(version)
    return version


def approve_version(version, note=""):
    version.status = "approved"
    version.review_note = note
    template = version.template
    template.approved_version = version
    template.updated_at = utcnow()
    session = object_session(version)
    if session is not None and template.id:
        # Alle aktiven Pässe der Vorlage bekommen das neue Design: Version erhöhen, Geräte benachrichtigen.
        now = utcnow()
        for p in session.scalars(select(Pass).where(Pass.template_id == template.id, Pass.status == "active")):
            p.version += 1
            p.updated_at = now
            jobs.enqueue_push(session, p)


def reject_version(version, note):
    version.status = "rejected"
    version.review_note = note


def get_template(session, tenant, template_id):
    t = session.get(Template, template_id)
    if t is None or t.tenant_id != tenant.id:
        raise ServiceError(404, "Vorlage nicht gefunden.")
    return t


def list_templates(session, tenant):
    return session.scalars(select(Template).where(Template.tenant_id == tenant.id)
                           .order_by(Template.created_at)).all()


# ---------------------------------------------------------------- Pässe

def platform_fields(pass_data, tenant, serial, auth_token, settings, voided, certificate=None):
    data = dict(pass_data)
    data["formatVersion"] = 1
    data["serialNumber"] = serial
    cert = certificate or tenant.certificate
    if cert is not None:
        data["passTypeIdentifier"] = cert.pass_type_identifier
        data["teamIdentifier"] = cert.team_identifier
    else:
        data.setdefault("passTypeIdentifier", "pass.example.unsigned")
        data.setdefault("teamIdentifier", "UNSIGNED00")
    data.setdefault("organizationName", tenant.organization_name)
    for key in ("authenticationToken", "webServiceURL"):
        data.pop(key, None)
    if settings is not None and settings.apple_web_service:
        data["webServiceURL"] = settings.public_base_url + "/"
        data["authenticationToken"] = auth_token
    if voided:
        data["voided"] = True
    return data


def _require_issuable(tenant, template):
    if tenant.status == "pending":
        raise ServiceError(403, "Das Konto wartet noch auf Freigabe.")
    if tenant.status != "active":
        raise ServiceError(403, "Das Konto ist gesperrt.")
    if tenant.certificate is None:
        raise ServiceError(409, "Für dieses Konto ist noch kein Zertifikat hinterlegt.")
    if template.approved_version is None:
        raise ServiceError(409, "Die Vorlage ist noch nicht freigegeben.")


def pass_certificate(pass_obj):
    """Zertifikat eines Passes: das bei der Ausgabe festgehaltene, für alte Pässe das der Firma."""
    return pass_obj.certificate or pass_obj.tenant.certificate


def build_pass(pass_obj, settings, signers, vault, source="api"):
    """Signierte .pkpass-Datei für einen Pass erzeugen (aktuelle freigegebene Vorlage)."""
    tenant, template = pass_obj.tenant, pass_obj.template
    _require_issuable(tenant, template)
    cert = pass_certificate(pass_obj)
    version = template.approved_version
    rendered = placeholders.render(version.pass_json, pass_obj.data)
    data = platform_fields(rendered, tenant, pass_obj.serial_number, vault.decrypt(pass_obj.auth_token_enc),
                            settings, pass_obj.status == "voided", certificate=cert)
    try:
        signer = signers.get(cert)
    except CertStoreError as exc:
        raise ServiceError(503, f"Signieren nicht möglich: {exc}") from exc
    try:
        with metrics.Timer(metrics.SIGNING_SECONDS):
            pkpass, _ = build_pkpass(data, version.file_map(), signer, strict=True)
    except BuildError as exc:
        raise ServiceError(422, "Der Pass ist mit diesen Daten ungültig.", exc.issues) from exc
    metrics.PKPASS_BUILDS.labels(source).inc()
    return pkpass


def _check_data(template, data):
    if not isinstance(data, dict):
        raise ServiceError(422, "data muss ein JSON-Objekt sein.")
    problems = placeholders.check(placeholders.find(template.approved_version.pass_json), data)
    if problems:
        raise ServiceError(422, " ".join(problems))


def create_pass(session, tenant, template_id, data, settings, signers, vault, serial_number=None,
                idempotency_key=None):
    """Neuen Pass anlegen. Rückgabe (pass, neu_angelegt)."""
    if idempotency_key:
        if len(idempotency_key) > 100:
            raise ServiceError(422, "Idempotency-Key ist zu lang (max. 100 Zeichen).")
        existing = session.scalars(select(Pass).where(Pass.tenant_id == tenant.id,
                                                      Pass.idempotency_key == idempotency_key)).one_or_none()
        if existing is not None:
            return existing, False
    template = get_template(session, tenant, template_id)
    _require_issuable(tenant, template)
    _check_data(template, data)
    if serial_number is None:
        serial_number = random_token(12)
        while serial_taken(session, tenant, serial_number):
            serial_number = random_token(12)
    elif not SERIAL_RE.match(str(serial_number)):
        raise ServiceError(422, "serial_number: 1-100 Zeichen, nur Buchstaben, Ziffern, Punkt, Minus, Unterstrich.")
    elif serial_taken(session, tenant, serial_number):
        raise ServiceError(409, f"Seriennummer {serial_number} ist bereits vergeben.")
    pass_obj = Pass(tenant=tenant, template=template, certificate=tenant.certificate,
                    serial_number=str(serial_number), data=data,
                    download_token=random_token(24), auth_token_enc=vault.encrypt(random_token(24)),
                    idempotency_key=idempotency_key)
    build_pass(pass_obj, settings, signers, vault, source="check")  # prüft vollständig, bevor gespeichert wird
    session.add(pass_obj)
    session.flush()
    metrics.PASSES_ISSUED.inc()
    return pass_obj, True


def serial_taken(session, tenant, serial):
    """Apple adressiert Pässe über Pass Type ID + Seriennummer: eindeutig je Zertifikat, nicht nur je Firma."""
    q = select(Pass.id).join(Tenant, Pass.tenant_id == Tenant.id).where(Pass.serial_number == str(serial))
    if tenant.certificate_id:
        q = q.where(func.coalesce(Pass.certificate_id, Tenant.certificate_id) == tenant.certificate_id)
    else:
        q = q.where(Pass.tenant_id == tenant.id)
    return session.scalars(q).first() is not None


def update_pass(session, pass_obj, data, settings, signers, vault):
    if pass_obj.status == "voided":
        raise ServiceError(409, "Ein gesperrter Pass kann nicht mehr geändert werden.")
    if not isinstance(data, dict) or not data:
        raise ServiceError(422, "data muss ein Objekt mit mindestens einem Feld sein.")
    merged = {**pass_obj.data, **data}
    _check_data(pass_obj.template, merged)
    old = pass_obj.data
    pass_obj.data = merged
    try:
        build_pass(pass_obj, settings, signers, vault, source="check")
    except ServiceError:
        pass_obj.data = old
        raise
    metrics.PASS_UPDATES.labels("update").inc()
    pass_obj.version += 1
    pass_obj.updated_at = utcnow()
    jobs.enqueue_push(session, pass_obj)
    jobs.emit(session, pass_obj.tenant_id, "pass.updated", jobs.pass_event_data(pass_obj))
    return pass_obj


def void_pass(session, pass_obj):
    if pass_obj.status != "voided":
        pass_obj.status = "voided"
        pass_obj.version += 1
        pass_obj.updated_at = utcnow()
        jobs.enqueue_push(session, pass_obj)
        jobs.emit(session, pass_obj.tenant_id, "pass.voided", jobs.pass_event_data(pass_obj))
        metrics.PASS_UPDATES.labels("void").inc()
    return pass_obj


def get_pass(session, tenant, pass_id):
    p = session.get(Pass, pass_id)
    if p is None or p.tenant_id != tenant.id:
        raise ServiceError(404, "Pass nicht gefunden.")
    return p


def list_passes(session, tenant, template_id=None, limit=50, offset=0):
    q = select(Pass).where(Pass.tenant_id == tenant.id)
    if template_id:
        q = q.where(Pass.template_id == template_id)
    return session.scalars(q.order_by(Pass.created_at.desc()).limit(limit).offset(offset)).all()


def pass_by_token(session, token):
    if not token or len(token) > 64:
        return None
    return session.scalars(select(Pass).where(Pass.download_token == token)).one_or_none()


def tenant_by_id(session, tenant_id) -> Tenant:
    t = session.get(Tenant, tenant_id)
    if t is None:
        raise ServiceError(404, "Firma nicht gefunden.")
    return t


def build_test_pass(tenant, version, data, signers):
    """Test-Pass aus einer (auch noch nicht freigegebenen) Vorlagen-Version, läuft nach 24 h ab."""
    if tenant.status == "suspended" or tenant.certificate is None:
        raise ServiceError(409, "Konto gesperrt oder noch kein Zertifikat hinterlegt.")
    names = placeholders.find(version.pass_json)
    data = {**placeholders.sample_data(names), **(data or {})}
    problems = placeholders.check(names, data)
    if problems:
        raise ServiceError(422, " ".join(problems))
    rendered = placeholders.render(version.pass_json, data)
    pass_data = platform_fields(rendered, tenant, "test-" + random_token(8), "x" * 16, None, False)
    pass_data["expirationDate"] = (utcnow() + timedelta(hours=24)).isoformat(timespec="seconds")
    try:
        signer = signers.get(tenant.certificate)
    except CertStoreError as exc:
        raise ServiceError(503, f"Signieren nicht möglich: {exc}") from exc
    try:
        pkpass, _ = build_pkpass(pass_data, version.file_map(), signer, strict=True)
    except BuildError as exc:
        raise ServiceError(422, "Die Vorlage ergibt mit diesen Werten keinen gültigen Pass.", exc.issues) from exc
    return pkpass


def create_api_key(session, tenant, name=""):
    """Rückgabe (Datensatz, vollständiger Schlüssel) - der Schlüssel wird nur einmal angezeigt."""
    key, prefix, secret_hash = generate_api_key()
    api_key = ApiKey(tenant_id=tenant.id, name=name[:100], prefix=prefix, secret_hash=secret_hash)
    session.add(api_key)
    session.flush()
    return api_key, key


def create_webhook(session, tenant, url, events, settings, vault):
    """Rückgabe (Endpunkt, Geheimnis) - das Geheimnis wird nur einmal angezeigt."""
    unknown = [e for e in events if e not in jobs.EVENTS]
    if not events or unknown:
        raise ServiceError(422, "events: mindestens eines von " + ", ".join(jobs.EVENTS))
    try:
        webhooks.check_url(url, settings.allow_insecure_webhooks)
    except webhooks.UnsafeUrl as exc:
        raise ServiceError(422, str(exc)) from exc
    if session.query(WebhookEndpoint).filter_by(tenant_id=tenant.id).count() >= 10:
        raise ServiceError(409, "Höchstens 10 Webhooks pro Konto.")
    secret = "whsec_" + random_token(24)
    ep = WebhookEndpoint(tenant_id=tenant.id, url=url, events=sorted(set(events)), secret_enc=vault.encrypt(secret))
    session.add(ep)
    session.flush()
    return ep, secret


def send_test_webhook(session, ep):
    jobs.enqueue(session, jobs.WEBHOOK, {"endpoint_id": ep.id, "body": {
        "id": "evt_test_" + random_token(8), "type": "webhook.test",
        "created_at": utcnow().isoformat(timespec="seconds"), "data": {}}}, tenant_id=ep.tenant_id)


__all__ = ["ServiceError", "Vault"]
