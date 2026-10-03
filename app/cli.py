"""Admin-Kommandos: ``python -m app <befehl>``.

Bis die Admin-Oberfläche steht (Phase 2), werden Firmen, API-Schlüssel, Zertifikate und
Freigaben hierüber verwaltet.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import make_engine, make_sessionmaker
from .models import ApiKey, Certificate, Template, TemplateVersion, Tenant, utcnow
from .security import Vault, generate_api_key


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
    t = _tenant(s, args.tenant)
    key, prefix, secret_hash = generate_api_key()
    s.add(ApiKey(tenant_id=t.id, name=args.name, prefix=prefix, secret_hash=secret_hash))
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
    try:
        cert = import_certificate(s, make_store(settings), Path(args.p12).read_bytes(), password, settings.wwdr_path)
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

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
