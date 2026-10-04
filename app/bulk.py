"""Massenausgabe aus CSV: Datei lesen, gegen die Vorlage prüfen, im Worker Pässe ausgeben."""

import csv
import io

from . import mailer, placeholders, services
from .models import BulkIssue, utcnow

MAX_ROWS = 5000
SERIAL_COLUMNS = ("serial_number", "seriennummer", "serial")
EMAIL_COLUMNS = ("email", "e-mail", "mail")
BULK = "bulk"


class CsvError(ValueError):
    pass


def read_csv(raw):
    """Bytes -> (Spalten, Zeilen als dicts). Erkennt Trennzeichen (; , Tab) und Kodierung (UTF-8, Windows)."""
    if not raw:
        raise CsvError("Die Datei ist leer.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise CsvError("Keine Kopfzeile gefunden. Die erste Zeile muss die Spaltennamen enthalten.")
    columns = [c.strip() for c in reader.fieldnames]
    rows = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        rows.append({k.strip(): (v or "").strip() for k, v in row.items() if k is not None})
        if len(rows) > MAX_ROWS:
            raise CsvError(f"Höchstens {MAX_ROWS} Zeilen pro Datei.")
    if not rows:
        raise CsvError("Die Datei enthält keine Datenzeilen.")
    return columns, rows


def plan_import(template, columns):
    """Zuordnung Spalte -> Platzhalter (Groß-/Kleinschreibung egal). Rückgabe (zuordnung, fehlend, extra)."""
    names = placeholders.find(template.approved_version.pass_json)
    lower = {c.lower(): c for c in columns}
    mapping = {n: lower[n.lower()] for n in names if n.lower() in lower}
    missing = [n for n in names if n not in mapping]
    special = {c for c in columns if c.lower() in SERIAL_COLUMNS + EMAIL_COLUMNS}
    extra = [c for c in columns if c not in mapping.values() and c not in special]
    return mapping, missing, extra


def _special(row, names):
    for k, v in row.items():
        if k.lower() in names and v:
            return v
    return None


def process(session, bulk_id, settings, signers, vault):
    """Vom Worker aufgerufen. Idempotent: Wiederholung erzeugt keine doppelten Pässe."""
    bulk = session.get(BulkIssue, bulk_id)
    if bulk is None or bulk.status == "done":
        return
    bulk.status = "running"
    session.commit()
    tenant, template = services.tenant_by_id(session, bulk.tenant_id), bulk.template
    mapping, _, _ = plan_import(template, list(bulk.rows[0].keys()) if bulk.rows else [])
    results = list(bulk.results or [])
    org = template.approved_version.pass_json.get("organizationName") or tenant.organization_name
    desc = template.approved_version.pass_json.get("description") or template.name
    for index in range(len(results), len(bulk.rows)):
        row = bulk.rows[index]
        data = {name: row.get(col, "") for name, col in mapping.items()}
        try:
            p, _ = services.create_pass(session, tenant, template.id, data, settings, signers, vault,
                                        serial_number=_special(row, SERIAL_COLUMNS),
                                        idempotency_key=f"bulk:{bulk.id}:{index}")
            page_url = f"{settings.public_base_url}/p/{p.download_token}"
            email = _special(row, EMAIL_COLUMNS)
            if bulk.send_emails and email:
                mailer.queue_mail(session, email, f"Dein Pass: {desc}", mailer.pass_text(org, desc, page_url),
                                  tenant_id=tenant.id)
            results.append({"row": index + 2, "status": "ok", "serial_number": p.serial_number, "page_url": page_url,
                            "download_url": page_url + "/pass.pkpass", "email": email or ""})
            bulk.done += 1
        except services.ServiceError as exc:
            session.rollback()
            bulk = session.get(BulkIssue, bulk_id)
            detail = "; ".join(i.message for i in exc.issues[:2])
            results.append({"row": index + 2, "status": "error", "error": exc.message + (f" ({detail})" if detail else "")})
            bulk.failed += 1
        if index % 25 == 0 or index == len(bulk.rows) - 1:
            bulk.results = list(results)
            session.commit()
    bulk.results = results
    bulk.status = "done"
    bulk.finished_at = utcnow()
    session.commit()


def result_csv(bulk):
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["zeile", "status", "seriennummer", "link", "download", "email", "fehler"])
    for r in bulk.results or []:
        w.writerow([r.get("row"), "ok" if r.get("status") == "ok" else "fehler", r.get("serial_number", ""),
                    r.get("page_url", ""), r.get("download_url", ""), r.get("email", ""), r.get("error", "")])
    return "﻿" + out.getvalue()  # BOM: Excel erkennt UTF-8
