"""Datenmodell der Plattform (Phase 1: Firmen, Schlüssel, Zertifikate, Vorlagen, Pässe)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def new_id():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class Tenant(Base):
    """Eine Kundenfirma."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    # Erscheint im Pass als organizationName, wenn die Vorlage keinen setzt.
    organization_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # pending | active | suspended
    plan: Mapped[str] = mapped_column(String(20), default="free")
    address: Mapped[str] = mapped_column(Text, default="")
    vat_id: Mapped[str] = mapped_column(String(40), default="")
    contact_email: Mapped[str] = mapped_column(String(200), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    # Zuletzt verschickte Kontingent-Warnung, z. B. "2026-10:80" - damit jede Warnung nur einmal pro Monat kommt
    quota_notice: Mapped[str] = mapped_column(String(20), default="")
    certificate_id: Mapped[str | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    # Vom Admin zugeordnetes Standard-Zertifikat - Rückfall, wenn die Firma ihr eigenes deaktiviert.
    standard_certificate_id: Mapped[str | None] = mapped_column(
        ForeignKey("certificates.id", name="fk_tenant_standard_certificate"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    certificate: Mapped["Certificate | None"] = relationship(foreign_keys=[certificate_id])
    standard_certificate: Mapped["Certificate | None"] = relationship(foreign_keys=[standard_certificate_id])


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship()


class Certificate(Base):
    """Ein Pass-Type-ID-Zertifikat. Der private Schlüssel liegt im Zertifikatsspeicher, nicht hier.

    Zwei Arten: Standard-Zertifikate aus dem Apple-Account des Plattform-Betreibers
    (``owner_tenant_id`` leer, beliebig vielen Firmen zuordenbar) und eigene Zertifikate einer
    Firma aus deren Apple-Account (``owner_tenant_id`` gesetzt, nur dieser Firma zuordenbar).
    """

    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pass_type_identifier: Mapped[str] = mapped_column(String(200), unique=True)
    team_identifier: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    owner_tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", use_alter=True, name="fk_certificate_owner_tenant"),
        nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def is_own(self):
        return self.owner_tenant_id is not None

    def usable_by(self, tenant):
        return self.owner_tenant_id in (None, tenant.id)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Die freigegebene Version, aus der Pässe gebaut werden (None = noch keine Freigabe).
    approved_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("template_versions.id", use_alter=True, name="fk_template_approved_version"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    versions: Mapped[list["TemplateVersion"]] = relationship(
        back_populates="template", foreign_keys="TemplateVersion.template_id",
        order_by="TemplateVersion.number", cascade="all, delete-orphan")
    approved_version: Mapped["TemplateVersion | None"] = relationship(foreign_keys=[approved_version_id],
                                                                      post_update=True)

    @property
    def latest_version(self):
        return self.versions[-1] if self.versions else None


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    pass_json: Mapped[dict] = mapped_column(JSONType)
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    template: Mapped[Template] = relationship(back_populates="versions", foreign_keys=[template_id])
    files: Mapped[list["TemplateFile"]] = relationship(cascade="all, delete-orphan", order_by="TemplateFile.name")

    def file_map(self):
        return {f.name: f.data for f in self.files}


class TemplateFile(Base):
    """Bild einer Vorlagen-Version (z. B. icon@2x.png)."""

    __tablename__ = "template_files"
    __table_args__ = (UniqueConstraint("version_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(ForeignKey("template_versions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Pass(Base):
    __tablename__ = "passes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "serial_number"),
        UniqueConstraint("tenant_id", "idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"), index=True)
    # Zertifikat, mit dem der Pass ausgegeben wurde. Apple erkennt Pässe an Pass Type ID + Seriennummer -
    # Updates müssen deshalb immer mit diesem Zertifikat signiert werden, auch wenn die Firma später wechselt.
    certificate_id: Mapped[str | None] = mapped_column(ForeignKey("certificates.id", name="fk_pass_certificate"),
                                                      nullable=True, index=True)
    serial_number: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | voided
    version: Mapped[int] = mapped_column(Integer, default=1)
    download_token: Mapped[str] = mapped_column(String(64), unique=True)
    # Token, mit dem sich das iPhone beim Apple-Web-Service ausweist (verschlüsselt gespeichert).
    auth_token_enc: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tenant: Mapped[Tenant] = relationship()
    template: Mapped[Template] = relationship()
    certificate: Mapped["Certificate | None"] = relationship()
    registrations: Mapped[list["Registration"]] = relationship(back_populates="pass_", cascade="all, delete-orphan")


# ---------------------------------------------------------------- Phase 2: Updates, Push, Webhooks

class Device(Base):
    """Ein iPhone bzw. eine Apple Watch, wie Apple sie beim Web-Service meldet."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    device_library_identifier: Mapped[str] = mapped_column(String(200), unique=True)
    push_token: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    registrations: Mapped[list["Registration"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Registration(Base):
    """Pass liegt in der Wallet dieses Geräts."""

    __tablename__ = "registrations"
    __table_args__ = (UniqueConstraint("device_id", "pass_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    pass_id: Mapped[str] = mapped_column(ForeignKey("passes.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    device: Mapped[Device] = relationship(back_populates="registrations")
    pass_: Mapped[Pass] = relationship(back_populates="registrations")


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    secret_enc: Mapped[str] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSONType, default=list)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """Hintergrundaufgabe (Push, Webhook). Wird in derselben Transaktion wie die Änderung
    angelegt, die sie auslöst - so geht keine Benachrichtigung verloren."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(40))
    # Firma, zu der der Job gehört - für die Ansicht je Firma im Admin-Bereich
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|running|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- Phase 2: Portal und Admin

class User(Base):
    """Person, die sich über Authentik anmeldet. Admins erkennt die App an der Authentik-Gruppe."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    oidc_sub: Mapped[str] = mapped_column(String(200), unique=True)
    email: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True,
                                                  index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # owner|admin|designer|issuer|viewer
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant | None] = relationship()


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    token: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship()


class AuditLog(Base):
    """Wer hat wann was getan - für Admin-Aktionen und wichtige Änderungen im Portal."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    object_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# ---------------------------------------------------------------- Betrieb und Komfort (Masterplan Phase 2/3)

class BulkIssue(Base):
    """Massenausgabe aus einer CSV-Datei, abgearbeitet vom Worker."""

    __tablename__ = "bulk_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(String(200), default="")
    filename: Mapped[str] = mapped_column(String(200), default="")
    rows: Mapped[list] = mapped_column(JSONType, default=list)
    send_emails: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | running | done | failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    template: Mapped[Template] = relationship()


class ApiRequestLog(Base):
    """Protokoll der API-Aufrufe einer Firma (für 'Einbinden' im Portal)."""

    __tablename__ = "api_request_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), default="")
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(300))
    status: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RateCounter(Base):
    """Zähler je Schlüssel und Minute für Rate-Limits (über alle Prozesse und Server hinweg)."""

    __tablename__ = "rate_counters"

    bucket: Mapped[str] = mapped_column(String(120), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)  # Unix-Minute
    count: Mapped[int] = mapped_column(Integer, default=0)


class UsageDaily(Base):
    """Tägliche Momentaufnahme je Firma - Grundlage für Abrechnung und Kontingent."""

    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("tenant_id", "day"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))  # JJJJ-MM-TT
    active: Mapped[int] = mapped_column(Integer, default=0)
    issued: Mapped[int] = mapped_column(Integer, default=0)
    installed: Mapped[int] = mapped_column(Integer, default=0)


class SystemState(Base):
    """Kleine Schlüssel-Wert-Ablage, z. B. wann die tägliche Wartung zuletzt lief."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Announcement(Base):
    """Hinweis vom Plattform-Admin, den alle Firmen im Portal sehen (z. B. Wartung)."""

    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    text: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(10), default="info")  # info | warn
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CertificateRequest(Base):
    """Zertifikatsanfrage (CSR) für Apple. Der private Schlüssel bleibt verschlüsselt hier, bis das
    Zertifikat von Apple hochgeladen wird - ohne Mac und Schlüsselbund."""

    __tablename__ = "certificate_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"),
                                                        nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    key_enc: Mapped[str] = mapped_column(Text)
    csr_pem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
