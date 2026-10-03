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
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    plan: Mapped[str] = mapped_column(String(20), default="free")
    certificate_id: Mapped[str | None] = mapped_column(ForeignKey("certificates.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    certificate: Mapped["Certificate | None"] = relationship()


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
    """Ein Pass-Type-ID-Zertifikat. Der private Schlüssel liegt im Zertifikatsspeicher, nicht hier."""

    __tablename__ = "certificates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pass_type_identifier: Mapped[str] = mapped_column(String(200), unique=True)
    team_identifier: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
