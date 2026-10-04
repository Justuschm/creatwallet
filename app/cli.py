"""Admin-Kommandos: ``python -m app <befehl>``.

Bis die Admin-Oberfläche steht (Phase 2), werden Firmen, API-Schlüssel, Zertifikate und
Freigaben hierüber verwaltet.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from .config import get_settings
from .db import make_engine, make_sessionmaker
from .models import ApiKey, Certificate, Template, TemplateVersion, Tenant, utcnow
from .security import Vault


def _session():
    settings = get_settings()
    return settings, make_sessionmaker(make_engine(settings.database_url))()


def _tenant(session, ident):
    t = session.get(Tenant, ident)
    if t is None:
        sys.exit(f"Firma {ident} nicht gefunden.")
    return t


def cmd_generate_secret(args):
    print(Vault.generate_key())


def cmd_migrate(args):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, args.revision)
    print("Datenbank ist auf dem neuesten Stand.")


def cmd_create_tenant(args):
    _, s = _session()
    t = Tenant(name=args.name, organization_name=args.organization or args.name, plan=args.plan)
    s.add(t)
    s.commit()
    print(f"Firma angelegt: {t.id}  ({t.name})")


def cmd_list_tenants(args):
    _, s = _session()
    for t in s.scalars(select(Tenant).order_by(Tenant.created_at)):
        pti = t.certificate.pass_type_identifier if t.certificate else "-"
        print(f"{t.id}  {t.status:<9} {t.plan:<9} {pti:<40} {t.name}")


def cmd_set_status(args):
    _, s = _session()
    t = _tenant(s, args.tenant)
    t.status = args.status
    s.commit()
    print(f"{t.name}: {t.status}")


def cmd_create_api_key(args):
    _, s = _session()
    from .services import create_api_key

    t = _tenant(s, args.tenant)
    _, key = create_api_key(s, t, args.name)
    s.commit()
    print(f"API-Schlüssel für {t.name} (wird nur jetzt angezeigt):\n\n  {key}\n")


def cmd_revoke_api_key(args):
    _, s = _session()
    k = s.scalars(select(ApiKey).where(ApiKey.prefix == args.prefix)).one_or_none()
    if k is None:
        sys.exit("Schlüssel nicht gefunden.")
    k.revoked_at = utcnow()
    s.commit()
    print(f"Schlüssel {args.prefix} widerrufen.")


def cmd_add_certificate(args):
    from .certs import CertStoreError, import_certificate, make_store

    settings, s = _session()
    if not settings.wwdr_path:
        sys.exit("WALLET_WWDR ist nicht gesetzt (Pfad zum Apple-WWDR-Zertifikat).")
    password = os.environ.get(args.password_env) if args.password_env else getpass.getpass(".p12-Passwort: ")
    if args.own and not args.tenant:
        sys.exit("--own braucht --tenant: das eigene Zertifikat gehört genau dieser Firma.")
    try:
        cert = import_certificate(s, make_store(settings), Path(args.p12).read_bytes(), password, settings.wwdr_path,
                                  owner_tenant_id=args.tenant if args.own else None)
    except CertStoreError as exc:
        sys.exit(f"Fehler: {exc}")
    if args.tenant:
        _tenant(s, args.tenant).certificate = cert
    s.commit()
    print(f"Zertifikat gespeichert: {cert.pass_type_identifier} (Team {cert.team_identifier}), "
          f"gültig bis {cert.expires_at:%d.%m.%Y}")


def cmd_assign_certificate(args):
    _, s = _session()
    cert = s.scalars(select(Certificate).where(Certificate.pass_type_identifier == args.pti)).one_or_none()
    if cert is None:
        sys.exit("Zertifikat nicht gefunden - zuerst add-certificate.")
    t = _tenant(s, args.tenant)
    if not cert.usable_by(t):
        sys.exit("Dieses Zertifikat gehört einer anderen Firma.")
    t.certificate = cert
    s.commit()
    print(f"{t.name} signiert jetzt mit {cert.pass_type_identifier}.")


def cmd_pending(args):
    _, s = _session()
    rows = s.execute(select(TemplateVersion, Template, Tenant).join(Template, TemplateVersion.template_id == Template.id)
                     .join(Tenant, Template.tenant_id == Tenant.id).where(TemplateVersion.status == "pending")
                     .order_by(TemplateVersion.created_at)).all()
    if not rows:
        print("Keine Vorlagen warten auf Freigabe.")
    for v, t, tenant in rows:
        print(f"{t.id}  v{v.number}  {tenant.name}: {t.name}")


def _version(s, template_id, number):
    t = s.get(Template, template_id)
    if t is None:
        sys.exit("Vorlage nicht gefunden.")
    v = next((v for v in t.versions if v.number == number), None) if number else t.latest_version
    if v is None:
        sys.exit("Version nicht gefunden.")
    return v


def cmd_approve(args):
    from .services import approve_version

    _, s = _session()
    v = _version(s, args.template, args.version)
    approve_version(v, args.note or "")
    s.commit()
    print(f"Freigegeben: {v.template.name} v{v.number}")


def cmd_reject(args):
    from .services import reject_version

    _, s = _session()
    v = _version(s, args.template, args.version)
    reject_version(v, args.note)
    s.commit()
    print(f"Abgelehnt: {v.template.name} v{v.number}")


def cmd_worker(args):
    import logging

    from .apns import ApnsPusher
    from .certs import make_store
    from .worker import Worker

    from .metrics import init_error_reporting, start_worker_metrics

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    init_error_reporting(settings.sentry_dsn, settings.environment, "worker")
    start_worker_metrics(settings.worker_metrics_port)
    Session = make_sessionmaker(make_engine(settings.database_url))
    from .certs import SignerProvider

    store = make_store(settings)
    worker = Worker(Session, settings, Vault(settings.secret_key),
                    ApnsPusher(store, settings.apns_host, settings.apns_push_type),
                    signers=SignerProvider(store, settings.wwdr_path))
    if args.once:
        print(f"{worker.run_until_empty()} Jobs erledigt.")
    else:
        worker.run_forever()


def cmd_daily(args):
    from .maintenance import run_daily

    settings, s = _session()
    print(run_daily(s, settings, force=True))


def cmd_jobs(args):
    from sqlalchemy import func

    from .models import Job

    _, s = _session()
    for status, n in s.execute(select(Job.status, func.count()).group_by(Job.status)).all():
        print(f"{status:<8} {n}")
    for j in s.scalars(select(Job).where(Job.status == "failed").order_by(Job.updated_at.desc()).limit(10)):
        print(f"  fehlgeschlagen {j.updated_at:%d.%m. %H:%M} {j.kind}: {j.last_error[:120]}")


def cmd_seed_demo(args):
    """Beispieldaten für den lokalen Testserver (nur mit WALLET_DEV_LOGIN=true)."""
    from creatwallet.templates import new_pass, placeholder_images

    from .certs import SignerProvider, make_store
    from .models import Pass, User
    from .services import approve_version, create_api_key, create_pass, create_template

    settings, s = _session()
    if not settings.dev_login:
        sys.exit("Nur für lokale Tests: WALLET_DEV_LOGIN=true setzen.")
    if s.scalars(select(Tenant).where(Tenant.name == "Kino am Markt GmbH")).first():
        sys.exit("Beispieldaten sind schon vorhanden.")
    cert = s.scalars(select(Certificate)).first()
    if cert is None:
        sys.exit("Zuerst ein Zertifikat hinzufügen: python -m app add-certificate --p12 …")

    kino = Tenant(name="Kino am Markt GmbH", organization_name="Kino am Markt", status="active", plan="business",
                  certificate=cert, contact_email="anna@kino.test", address="Marktplatz 1\n20095 Hamburg")
    zoo = Tenant(name="Tierpark Nord GmbH", organization_name="Tierpark Nord", status="pending",
                 contact_email="eva@tierpark.test")
    s.add_all([kino, zoo])
    s.flush()
    s.add_all([
        User(oidc_sub="dev:admin@plattform.test", email="admin@plattform.test", name="Plattform-Admin", is_admin=True),
        User(oidc_sub="dev:anna@kino.test", email="anna@kino.test", name="Anna Kino", tenant_id=kino.id, role="owner"),
        User(oidc_sub="dev:ben@kino.test", email="ben@kino.test", name="Ben Kasse", tenant_id=kino.id, role="issuer"),
        User(oidc_sub="dev:eva@tierpark.test", email="eva@tierpark.test", name="Eva Tierpark", tenant_id=zoo.id,
             role="owner"),
    ])

    ticket = new_pass("posterEventTicket")
    ticket["description"] = "Kinoticket"
    ticket["eventLogoText"] = ticket["logoText"] = "Kino am Markt"
    ticket["organizationName"] = "Kino am Markt"
    sem = ticket["semantics"]
    sem.update({"eventName": "{{film}}", "venueName": "Kino am Markt", "venueRegionName": "Hamburg",
                "venueRoom": "{{saal}}", "eventStartDate": "{{beginn}}", "attendeeName": "{{name}}",
                "performerNames": ["Kino am Markt"]})
    sem["eventStartDateInfo"] = {"date": "{{beginn}}", "timeZone": "Europe/Berlin"}
    sem.pop("eventEndDate", None)
    sem["seats"] = [{"seatRow": "{{reihe}}", "seatNumber": "{{platz}}", "seatSectionColor": "rgb(200, 30, 60)"}]
    ticket["eventTicket"]["primaryFields"] = [{"key": "film", "label": "FILM", "value": "{{film}}"}]
    ticket["eventTicket"]["secondaryFields"] = [
        {"key": "saal", "label": "SAAL", "value": "{{saal}}"},
        {"key": "beginn", "label": "BEGINN", "value": "{{beginn}}", "dateStyle": "PKDateStyleMedium",
         "timeStyle": "PKDateStyleShort"}]
    ticket["eventTicket"]["auxiliaryFields"] = [{"key": "reihe", "label": "REIHE", "value": "{{reihe}}"},
                                                {"key": "platz", "label": "PLATZ", "value": "{{platz}}"}]
    ticket["barcodes"] = [{"format": "PKBarcodeFormatQR", "message": "{{ticket}}", "messageEncoding": "iso-8859-1",
                           "altText": "{{ticket}}"}]
    for key in ("relevantDates", "transferURL", "merchandiseURL"):
        ticket.pop(key, None)
    template, version = create_template(s, kino, "Kinoticket", ticket,
                                        placeholder_images("posterEventTicket", scales=(2, 3)), settings)
    approve_version(version, "Beispiel")

    signers = SignerProvider(make_store(settings), settings.wwdr_path)
    from .security import Vault
    vault = Vault(settings.secret_key)
    films = [("Die Beispiele", "Saal 1", "2026-11-20T20:00+01:00", "Anna Beispiel", "5", "12"),
             ("Hafenlichter", "Saal 2", "2026-11-21T18:30+01:00", "Max Mustermann", "8", "3"),
             ("Nordwind", "Saal 1", "2026-11-22T21:00+01:00", "Lena Muster", "2", "7")]
    for i, (film, saal, beginn, name, reihe, platz) in enumerate(films, 1):
        create_pass(s, kino, template.id, {"film": film, "saal": saal, "beginn": beginn, "name": name,
                                           "reihe": reihe, "platz": platz, "ticket": f"KAM-{1000 + i}"},
                    settings, signers, vault)
    s.flush()
    _, key = create_api_key(s, kino, "Beispiel-Shop")
    s.commit()
    count = s.scalar(select(func.count(Pass.id)))
    print(f"Beispieldaten angelegt: 2 Firmen, 4 Zugänge, 1 Vorlage, {count} Pässe.")
    print(f"API-Schlüssel der Firma Kino am Markt:\n  {key}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m app", description="Verwaltung der Wallet-Pass-Plattform")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("generate-secret", help="Schlüssel für WALLET_SECRET_KEY erzeugen").set_defaults(func=cmd_generate_secret)
    m = sub.add_parser("migrate", help="Datenbank anlegen bzw. aktualisieren")
    m.add_argument("revision", nargs="?", default="head")
    m.set_defaults(func=cmd_migrate)

    c = sub.add_parser("create-tenant", help="Firma anlegen")
    c.add_argument("--name", required=True)
    c.add_argument("--organization", help="organizationName im Pass (Standard: --name)")
    c.add_argument("--plan", default="free")
    c.set_defaults(func=cmd_create_tenant)
    sub.add_parser("list-tenants", help="Firmen auflisten").set_defaults(func=cmd_list_tenants)
    st = sub.add_parser("set-status", help="Firma sperren oder freischalten")
    st.add_argument("tenant")
    st.add_argument("status", choices=["active", "suspended"])
    st.set_defaults(func=cmd_set_status)

    k = sub.add_parser("create-api-key", help="API-Schlüssel erzeugen")
    k.add_argument("--tenant", required=True)
    k.add_argument("--name", default="")
    k.set_defaults(func=cmd_create_api_key)
    r = sub.add_parser("revoke-api-key", help="API-Schlüssel widerrufen")
    r.add_argument("prefix", help="mittlerer Teil von wk_<präfix>_…")
    r.set_defaults(func=cmd_revoke_api_key)

    a = sub.add_parser("add-certificate", help=".p12-Zertifikat prüfen und speichern")
    a.add_argument("--p12", required=True)
    a.add_argument("--password-env", help="Passwort aus dieser Umgebungsvariable lesen (sonst Abfrage)")
    a.add_argument("--tenant", help="direkt dieser Firma zuordnen")
    a.add_argument("--own", action="store_true", help="eigenes Zertifikat der Firma (aus deren Apple-Account)")
    a.set_defaults(func=cmd_add_certificate)
    ac = sub.add_parser("assign-certificate", help="Zertifikat einer Firma zuordnen")
    ac.add_argument("--tenant", required=True)
    ac.add_argument("--pti", required=True, help="Pass Type ID")
    ac.set_defaults(func=cmd_assign_certificate)

    sub.add_parser("pending", help="Vorlagen, die auf Freigabe warten").set_defaults(func=cmd_pending)
    ap = sub.add_parser("approve", help="Vorlage freigeben")
    ap.add_argument("template")
    ap.add_argument("--version", type=int)
    ap.add_argument("--note")
    ap.set_defaults(func=cmd_approve)
    rj = sub.add_parser("reject", help="Vorlage ablehnen")
    rj.add_argument("template")
    rj.add_argument("--version", type=int)
    rj.add_argument("--note", required=True)
    rj.set_defaults(func=cmd_reject)

    w = sub.add_parser("worker", help="Hintergrund-Worker (Push, Webhooks) starten")
    w.add_argument("--once", action="store_true", help="nur fällige Jobs abarbeiten und beenden")
    w.set_defaults(func=cmd_worker)
    sub.add_parser("jobs", help="Status der Hintergrundaufgaben").set_defaults(func=cmd_jobs)
    sub.add_parser("daily", help="Tägliche Wartung jetzt ausführen").set_defaults(func=cmd_daily)
    sub.add_parser("seed-demo", help="Beispieldaten für den lokalen Testserver").set_defaults(func=cmd_seed_demo)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
