"""Command line interface: ``creatwallet <command>``."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from . import __version__
from . import validation as v
from .build import BuildError, build_pkpass, inspect_pkpass, load_bundle
from .sign import SigningError, load_signer
from .templates import TEMPLATES, write_project

_COLORS = {v.ERROR: "\033[31m", v.WARNING: "\033[33m", v.INFO: "\033[36m"}
_LABELS = {v.ERROR: "FEHLER ", v.WARNING: "WARNUNG", v.INFO: "HINWEIS"}


def print_issues(issues, show_info=True, stream=None):
    stream = stream or sys.stdout
    color = stream.isatty()
    shown = [i for i in issues if show_info or i.level != v.INFO]
    for issue in shown:
        label = _LABELS[issue.level]
        if color:
            label = f"{_COLORS[issue.level]}{label}\033[0m"
        print(f"  {label}  {issue.path}: {issue.message}", file=stream)
    counts = {lvl: sum(1 for i in issues if i.level == lvl) for lvl in _LABELS}
    print(f"  -> {counts[v.ERROR]} Fehler, {counts[v.WARNING]} Warnungen, {counts[v.INFO]} Hinweise",
          file=stream)


def deep_merge(base, extra):
    """Merge ``extra`` into ``base``. Dicts merge recursively, everything else replaces."""
    result = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_set(items):
    """--set key=value (value is parsed as JSON if possible). Dots address nested keys."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--set erwartet key=value, nicht '{item}'")
        key, raw = item.split("=", 1)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def env(name):
    """Value of ``name`` or the content of the file in ``name_FILE`` (Docker secrets)."""
    path = os.environ.get(name + "_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name)


def add_signing_args(parser):
    g = parser.add_argument_group("Signatur (alternativ per Umgebungsvariable)")
    g.add_argument("--p12", default=os.environ.get("CREATWALLET_P12"),
                   help="Pass-Type-ID-Zertifikat inkl. Schlüssel als .p12 [CREATWALLET_P12]")
    g.add_argument("--password", default=env("CREATWALLET_PASSWORD"),
                   help="Passwort der .p12 bzw. des Schlüssels [CREATWALLET_PASSWORD bzw. _FILE]")
    g.add_argument("--cert", default=os.environ.get("CREATWALLET_CERT"),
                   help="Zertifikat als PEM (statt --p12) [CREATWALLET_CERT]")
    g.add_argument("--key", default=os.environ.get("CREATWALLET_KEY"),
                   help="Privater Schlüssel als PEM (statt --p12) [CREATWALLET_KEY]")
    g.add_argument("--wwdr", default=os.environ.get("CREATWALLET_WWDR"),
                   help="Apple WWDR G4 Zwischenzertifikat [CREATWALLET_WWDR]")


def signer_from_args(args):
    return load_signer(args.wwdr, p12=args.p12, cert=args.cert, key=args.key, password=args.password)


# --------------------------------------------------------------------------

def cmd_templates(args):
    print("Verfügbare Vorlagen:\n")
    for name, t in TEMPLATES.items():
        print(f"  {name:22} ab iOS {t['min_ios']:>2}  {t['title']}")
    print("\nNeues Projekt: creatwallet init <vorlage> <ordner>")


def cmd_init(args):
    if args.template not in TEMPLATES:
        print(f"Unbekannte Vorlage '{args.template}'. Siehe: creatwallet templates", file=sys.stderr)
        return 2
    target = write_project(args.template, args.directory, args.pass_type_id, args.team_id)
    print(f"Projekt angelegt: {target}")
    print("  1. pass.json anpassen (passTypeIdentifier / teamIdentifier aus deinem Apple-Developer-Konto)")
    print("  2. Platzhalter-Bilder (*.png) durch eigene ersetzen")
    print(f"  3. creatwallet build {target} -o mein-pass.pkpass --p12 zertifikat.p12 --wwdr AppleWWDRCAG4.cer")
    return 0


def cmd_validate(args):
    path = Path(args.path)
    if path.suffix == ".pkpass":
        return cmd_inspect(args)
    if path.is_dir():
        pass_data, files = load_bundle(path)
    else:
        pass_data, files = json.loads(path.read_text(encoding="utf-8")), None
    issues = v.validate(pass_data, files)
    print(f"Prüfe {path}")
    print_issues(issues, show_info=not args.quiet)
    return 1 if v.has_errors(issues) else 0


def cmd_build(args):
    pass_data, files = load_bundle(args.directory)
    if args.merge:
        pass_data = deep_merge(pass_data, json.loads(Path(args.merge).read_text(encoding="utf-8")))
    overrides = _parse_set(args.set)
    if args.new_serial:
        overrides["serialNumber"] = uuid.uuid4().hex
    pass_data = deep_merge(pass_data, overrides)
    signer = signer_from_args(args)
    output = Path(args.output or Path(args.directory).with_suffix(".pkpass").name)
    try:
        data, issues = build_pkpass(pass_data, files, signer, strict=not args.force)
    except BuildError as exc:
        print(f"Build abgebrochen: {exc}", file=sys.stderr)
        print_issues(exc.issues, show_info=False, stream=sys.stderr)
        return 1
    output.write_bytes(data)
    print_issues(issues, show_info=not args.quiet)
    print(f"Fertig: {output} ({len(data) // 1024} KB)")
    return 0


def cmd_inspect(args):
    info = inspect_pkpass(Path(args.path).read_bytes())
    print(f"Inhalt von {args.path}:")
    for name in info["files"]:
        print(f"  - {name}")
    if info["certificates"]:
        print("Zertifikate in der Signatur:")
        for subject in info["certificates"]:
            print(f"  - {subject}")
    for problem in info["problems"]:
        print(f"  FEHLER   {problem}")
    print_issues(info["issues"], show_info=not getattr(args, "quiet", False))
    if getattr(args, "json", False) and info["pass"] is not None:
        print(json.dumps(info["pass"], indent=2, ensure_ascii=False))
    return 1 if info["problems"] or v.has_errors(info["issues"]) else 0


def cmd_serve(args):
    from .server import serve

    signer = None
    if args.wwdr and (args.p12 or (args.cert and args.key)):
        signer = signer_from_args(args)
    else:
        print("Hinweis: Kein Zertifikat angegeben - Editor & Prüfung funktionieren, "
              "Signieren erst mit --p12/--cert/--key und --wwdr.")
    if args.auth and ":" not in args.auth:
        print("Fehler: --auth erwartet benutzer:passwort", file=sys.stderr)
        return 2
    serve(args.host, args.port, signer, args.auth)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="creatwallet",
        description="Apple-Wallet-Pässe (.pkpass) erstellen, prüfen und signieren - "
                    "inkl. Poster-Event-Tickets, semantischer Boarding-Pässe und Poster-Generic-Pässe.")
    parser.add_argument("--version", action="version", version=f"creatwallet {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("templates", help="Vorlagen auflisten")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("init", help="Neues Pass-Projekt aus einer Vorlage anlegen")
    p.add_argument("template")
    p.add_argument("directory")
    p.add_argument("--pass-type-id", help="z. B. pass.de.meinefirma.ticket")
    p.add_argument("--team-id", help="10-stellige Team ID aus dem Developer-Konto")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate", help="Projektordner, pass.json oder .pkpass prüfen")
    p.add_argument("path")
    p.add_argument("-q", "--quiet", action="store_true", help="Hinweise ausblenden")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("build", help="Projektordner zu signierter .pkpass bauen")
    p.add_argument("directory")
    p.add_argument("-o", "--output", help="Ausgabedatei (Standard: <ordner>.pkpass)")
    p.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="Wert überschreiben, z. B. --set serialNumber=42 --set semantics.attendeeName=Anna")
    p.add_argument("--merge", metavar="JSON", help="JSON-Datei, die in pass.json gemischt wird (Personalisierung)")
    p.add_argument("--new-serial", action="store_true", help="Zufällige neue Seriennummer vergeben")
    p.add_argument("--force", action="store_true", help="Trotz Fehlern bauen")
    p.add_argument("-q", "--quiet", action="store_true", help="Hinweise ausblenden")
    add_signing_args(p)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("inspect", help="Bestehende .pkpass analysieren")
    p.add_argument("path")
    p.add_argument("--json", action="store_true", help="pass.json ausgeben")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("serve", help="Web-Editor im Browser starten")
    p.add_argument("--host", default=os.environ.get("CREATWALLET_HOST", "127.0.0.1"),
                   help="Adresse, auf der gelauscht wird (Docker: 0.0.0.0) [CREATWALLET_HOST]")
    p.add_argument("--port", type=int, default=int(os.environ.get("CREATWALLET_PORT", "8080")),
                   help="[CREATWALLET_PORT]")
    p.add_argument("--auth", default=env("CREATWALLET_AUTH"), metavar="BENUTZER:PASSWORT",
                   help="Zugang per Passwort schützen [CREATWALLET_AUTH bzw. _FILE]")
    add_signing_args(p)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (BuildError, SigningError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        if isinstance(exc, BuildError):
            print_issues(exc.issues, show_info=False, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
