"""Building (.pass directory -> .pkpass) and inspecting pass bundles."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from . import validation as v

IGNORED_FILES = {"manifest.json", "signature", ".DS_Store", "Thumbs.db"}


class BuildError(Exception):
    def __init__(self, message, issues=None):
        super().__init__(message)
        self.issues = issues or []


def load_bundle(directory):
    """Read a pass source directory. Returns (pass_data, files)."""
    root = Path(directory)
    pass_file = root / "pass.json"
    if not pass_file.is_file():
        raise BuildError(f"{pass_file} nicht gefunden.")
    try:
        pass_data = json.loads(pass_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildError(f"pass.json ist kein gültiges JSON: {exc}") from exc
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "pass.json" or path.name in IGNORED_FILES or path.name.startswith("."):
            continue
        files[rel] = path.read_bytes()
    return pass_data, files


def make_manifest(file_bytes):
    """manifest.json content: SHA-1 hash of every file in the bundle."""
    manifest = {name: hashlib.sha1(data).hexdigest() for name, data in sorted(file_bytes.items())}
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def build_pkpass(pass_data, files, signer, *, overrides=None, strict=True):
    """Create a signed .pkpass and return (pkpass_bytes, issues).

    ``overrides`` are merged into the top level of pass.json (e.g. a new
    serialNumber). passTypeIdentifier and teamIdentifier are taken from the
    certificate when missing. With ``strict`` the build fails on errors.
    """
    pass_data = dict(pass_data)
    pass_data.update(overrides or {})

    cert_pti, cert_team = signer.pass_type_identifier, signer.team_identifier
    if cert_pti and not pass_data.get("passTypeIdentifier"):
        pass_data["passTypeIdentifier"] = cert_pti
    if cert_team and not pass_data.get("teamIdentifier"):
        pass_data["teamIdentifier"] = cert_team

    issues = v.validate(pass_data, files)
    if cert_pti and pass_data.get("passTypeIdentifier") != cert_pti:
        issues.append(v.Issue(v.ERROR, "passTypeIdentifier",
                              f"Passt nicht zum Zertifikat ({cert_pti}) - Wallet lehnt den Pass ab."))
    if cert_team and pass_data.get("teamIdentifier") != cert_team:
        issues.append(v.Issue(v.ERROR, "teamIdentifier",
                              f"Passt nicht zum Zertifikat ({cert_team}) - Wallet lehnt den Pass ab."))
    if strict and v.has_errors(issues):
        raise BuildError("Der Pass enthält Fehler.", issues)

    bundle = dict(files)
    bundle["pass.json"] = json.dumps(pass_data, indent=2, ensure_ascii=False).encode("utf-8")
    manifest = make_manifest(bundle)
    signature = signer.sign(manifest)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(bundle):
            zf.writestr(name, bundle[name])
        zf.writestr("manifest.json", manifest)
        zf.writestr("signature", signature)
    return out.getvalue(), issues


def inspect_pkpass(data):
    """Inspect a .pkpass. Returns a dict with pass.json, files and problems."""
    problems = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BuildError(f"Keine gültige .pkpass-(ZIP-)Datei: {exc}") from exc
    contents = {name: zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    for required in ("pass.json", "manifest.json", "signature"):
        if required not in contents:
            problems.append(f"{required} fehlt.")
    manifest = {}
    if "manifest.json" in contents:
        manifest = json.loads(contents["manifest.json"])
        for name, digest in manifest.items():
            if name not in contents:
                problems.append(f"{name} steht im Manifest, fehlt aber.")
            elif hashlib.sha1(contents[name]).hexdigest() != digest:
                problems.append(f"Hash von {name} stimmt nicht mit dem Manifest überein.")
        for name in contents:
            if name not in manifest and name not in ("manifest.json", "signature"):
                problems.append(f"{name} fehlt im Manifest.")
    pass_data = json.loads(contents["pass.json"]) if "pass.json" in contents else None
    files = {k: b for k, b in contents.items() if k not in ("pass.json", "manifest.json", "signature")}
    signer_subject = None
    if "signature" in contents:
        try:
            from cryptography.hazmat.primitives.serialization import pkcs7
            certs = pkcs7.load_der_pkcs7_certificates(contents["signature"])
            signer_subject = [c.subject.rfc4514_string() for c in certs]
        except Exception as exc:  # noqa: BLE001 - report any parse failure
            problems.append(f"Signatur nicht lesbar: {exc}")
    return {
        "pass": pass_data,
        "files": sorted(contents),
        "problems": problems,
        "certificates": signer_subject,
        "images": {k: b for k, b in files.items() if k.endswith(".png")},
        "issues": v.validate(pass_data, files) if pass_data is not None else [],
    }
