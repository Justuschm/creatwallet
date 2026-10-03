"""Ablage der Signier-Zertifikate (.p12) und daraus erzeugte Signer.

Zwei Speicher: ``file`` legt jedes Zertifikat verschlüsselt (Fernet, WALLET_SECRET_KEY)
in einem Verzeichnis ab - für Pilot und Entwicklung. ``openbao`` nutzt die KV-v2-Engine
von OpenBao (API-kompatibel zu HashiCorp Vault) - für die Produktion.
"""

import base64
import json
import os
import threading
from pathlib import Path

import httpx

from creatwallet.sign import SigningError, load_signer

from .models import Certificate
from .security import Vault


class CertStoreError(Exception):
    pass


def _safe_name(pti):
    if not pti or "/" in pti or ".." in pti or pti.startswith("."):
        raise CertStoreError(f"Ungültige Pass Type ID: {pti!r}")
    return pti


class FileCertStore:
    def __init__(self, directory, vault):
        self.dir = Path(directory)
        self.vault = vault

    def _path(self, pti):
        return self.dir / f"{_safe_name(pti)}.cert"

    def save(self, pti, p12, password):
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        payload = json.dumps({"p12": base64.b64encode(p12).decode(), "password": password or ""})
        path = self._path(pti)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self.vault.encrypt(payload))

    def load(self, pti):
        path = self._path(pti)
        if not path.is_file():
            raise CertStoreError(f"Zertifikat für {pti} nicht im Speicher ({path}).")
        data = json.loads(self.vault.decrypt(path.read_text()))
        return base64.b64decode(data["p12"]), data["password"]


class OpenBaoCertStore:
    def __init__(self, addr, token, mount="secret", prefix="wallet/certs", client=None):
        if not token:
            raise CertStoreError("WALLET_OPENBAO_TOKEN fehlt.")
        self.url = f"{addr}/v1/{mount}/data/{prefix.strip('/')}"
        self.client = client or httpx.Client(timeout=10, headers={"X-Vault-Token": token})

    def save(self, pti, p12, password):
        r = self.client.post(f"{self.url}/{_safe_name(pti)}",
                             json={"data": {"p12": base64.b64encode(p12).decode(), "password": password or ""}})
        if r.status_code >= 300:
            raise CertStoreError(f"OpenBao: Speichern fehlgeschlagen ({r.status_code}): {r.text[:200]}")

    def load(self, pti):
        r = self.client.get(f"{self.url}/{_safe_name(pti)}")
        if r.status_code == 404:
            raise CertStoreError(f"Zertifikat für {pti} nicht in OpenBao.")
        if r.status_code >= 300:
            raise CertStoreError(f"OpenBao: Lesen fehlgeschlagen ({r.status_code}): {r.text[:200]}")
        data = r.json()["data"]["data"]
        return base64.b64decode(data["p12"]), data["password"]


def make_store(settings):
    if settings.cert_backend == "openbao":
        return OpenBaoCertStore(settings.openbao_addr, settings.openbao_token,
                                settings.openbao_mount, settings.openbao_prefix)
    if settings.cert_backend == "file":
        return FileCertStore(settings.cert_dir, Vault(settings.secret_key))
    raise CertStoreError(f"Unbekannter Zertifikatsspeicher: {settings.cert_backend}")


class SignerProvider:
    """Lädt Signer bei Bedarf aus dem Speicher und hält sie im Arbeitsspeicher vor."""

    def __init__(self, store, wwdr_path):
        self.store = store
        self.wwdr_path = wwdr_path
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, certificate: Certificate):
        pti = certificate.pass_type_identifier
        with self._lock:
            signer = self._cache.get(pti)
            if signer is None:
                if not self.wwdr_path:
                    raise CertStoreError("WALLET_WWDR (Apple-WWDR-Zertifikat) ist nicht gesetzt.")
                p12, password = self.store.load(pti)
                signer = load_signer(self.wwdr_path, p12=p12, password=password)
                self._cache[pti] = signer
            return signer

    def forget(self, pti):
        with self._lock:
            self._cache.pop(pti, None)


def import_certificate(session, store, p12, password, wwdr_path):
    """Prüft eine .p12-Datei, legt sie im Speicher ab und gibt den Datenbankeintrag zurück."""
    try:
        signer = load_signer(wwdr_path, p12=p12, password=password)
    except SigningError as exc:
        raise CertStoreError(str(exc)) from exc
    pti, team = signer.pass_type_identifier, signer.team_identifier
    if not pti or not team:
        raise CertStoreError("Das Zertifikat enthält keine Pass Type ID bzw. Team ID - ist es ein Pass-Zertifikat?")
    store.save(pti, p12, password)
    cert = session.query(Certificate).filter_by(pass_type_identifier=pti).one_or_none()
    if cert is None:
        cert = Certificate(pass_type_identifier=pti, team_identifier=team,
                           expires_at=signer.certificate.not_valid_after_utc)
        session.add(cert)
    else:
        cert.team_identifier = team
        cert.expires_at = signer.certificate.not_valid_after_utc
    session.flush()
    return cert
