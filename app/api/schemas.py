"""Ein- und Ausgabeformate der Kunden-API (erscheinen so in der OpenAPI-Doku)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TemplateCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, json_schema_extra={"examples": [
        {"name": "Konzertticket", "base_template": "posterEventTicket"},
        {"name": "Mitgliedskarte", "pass": {"description": "Mitgliedskarte", "generic": {
            "primaryFields": [{"key": "name", "label": "MITGLIED", "value": "{{name}}"}]}},
         "images": {"icon@2x.png": "<base64>"}},
    ]})

    name: str = Field(max_length=200, description="Interner Name der Vorlage")
    pass_json: dict[str, Any] | None = Field(
        None, alias="pass",
        description="pass.json mit Platzhaltern wie {{name}}. serialNumber, passTypeIdentifier, "
                    "teamIdentifier, webServiceURL und authenticationToken setzt die Plattform.")
    images: dict[str, str] | None = Field(None, description="Bilder als base64, z. B. {'icon@2x.png': '...'}")
    base_template: str | None = Field(None, description="Startvorlage aus creatwallet, z. B. posterEventTicket")


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(None, max_length=200)
    pass_json: dict[str, Any] | None = Field(None, alias="pass")
    images: dict[str, str] | None = None


class VersionOut(BaseModel):
    number: int
    status: str
    review_note: str
    created_at: datetime


class IssueOut(BaseModel):
    level: str
    path: str
    message: str


class TemplateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    status: str = Field(description="approved = Pässe können ausgegeben werden; pending = wartet auf Freigabe")
    approved_version: int | None
    latest_version: VersionOut
    placeholders: list[str] = Field(description="Felder, die beim Ausgeben in data stehen müssen")
    images: list[str]
    created_at: datetime
    updated_at: datetime
    pass_json: dict[str, Any] | None = Field(None, alias="pass")
    issues: list[IssueOut] | None = None


class PassCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"template_id": "…", "data": {"name": "Anna Beispiel", "sitzplatz": "12"}},
    ]})

    template_id: str
    data: dict[str, Any] = Field(default_factory=dict, description="Werte für die Platzhalter der Vorlage")
    serial_number: str | None = Field(None, description="Eigene Seriennummer; sonst wird eine erzeugt")


class PassUpdate(BaseModel):
    data: dict[str, Any] = Field(description="Geänderte Felder; nicht genannte bleiben erhalten")


class TestPassRequest(BaseModel):
    data: dict[str, Any] | None = Field(None, description="Beispielwerte; fehlende werden ergänzt")


class PassOut(BaseModel):
    id: str
    serial_number: str
    template_id: str
    status: str = Field(description="active oder voided")
    version: int
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    installed_devices: int = Field(0, description="Anzahl Geräte, auf denen der Pass in der Wallet liegt")
    page_url: str = Field(description="Seite für den Endkunden mit Button und QR-Code")
    download_url: str = Field(description="Direkter Download der .pkpass-Datei")


class PassList(BaseModel):
    items: list[PassOut]
    limit: int
    offset: int


class AccountOut(BaseModel):
    id: str
    name: str
    organization_name: str
    status: str
    plan: str
    pass_type_identifier: str | None
    certificate_expires_at: datetime | None


class ErrorOut(BaseModel):
    error: str
    issues: list[IssueOut] = []


class WebhookCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [
        {"url": "https://shop.example.de/hooks/wallet", "events": ["pass.installed", "pass.removed"]}]})

    url: str = Field(max_length=2000, description="https-Adresse, an die Ereignisse per POST gehen")
    events: list[str] = Field(description="pass.installed, pass.removed, pass.updated, pass.voided")


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime
    secret: str | None = Field(None, description="Nur beim Anlegen: zum Prüfen der Signatur (Header Wallet-Signature)")
