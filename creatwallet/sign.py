"""Signing of the pass manifest (PKCS#7 detached signature)."""

from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7, pkcs12
from cryptography.x509.oid import NameOID

# Apple WWDR intermediate (G4) - must be included in every pass signature.
WWDR_G4_URL = "https://www.apple.com/certificateauthority/AppleWWDRCAG4.cer"


class SigningError(Exception):
    pass


@dataclass
class Signer:
    certificate: x509.Certificate
    private_key: object
    wwdr: x509.Certificate

    @property
    def pass_type_identifier(self):
        """The pass type ID from the certificate's UID attribute (if present)."""
        attrs = self.certificate.subject.get_attributes_for_oid(NameOID.USER_ID)
        return attrs[0].value if attrs else None

    @property
    def team_identifier(self):
        attrs = self.certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
        return attrs[0].value if attrs else None

    def sign(self, manifest_bytes):
        """Return the DER encoded detached signature of ``manifest_bytes``."""
        return (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(manifest_bytes)
            .add_signer(self.certificate, self.private_key, hashes.SHA256())
            .add_certificate(self.wwdr)
            .sign(serialization.Encoding.DER,
                  [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])
        )


def _load_cert(data):
    try:
        if b"-----BEGIN" in data:
            return x509.load_pem_x509_certificate(data)
        return x509.load_der_x509_certificate(data)
    except ValueError as exc:
        raise SigningError(f"Zertifikat konnte nicht gelesen werden: {exc}") from exc


def _password(password):
    if password is None or password == "":
        return None
    return password.encode() if isinstance(password, str) else password


def load_signer(wwdr, p12=None, cert=None, key=None, password=None):
    """Load signing material.

    Either ``p12`` (the .p12 exported from Keychain) or ``cert`` + ``key``
    (PEM files) must be given. ``wwdr`` is Apple's WWDR intermediate
    certificate (.cer/.pem). Paths or bytes are accepted.
    """
    def read(src):
        return src if isinstance(src, bytes) else Path(src).read_bytes()

    if wwdr is None:
        raise SigningError(f"Das Apple-WWDR-Zertifikat fehlt (Download: {WWDR_G4_URL}).")
    wwdr_cert = _load_cert(read(wwdr))

    if p12 is not None:
        try:
            private_key, certificate, _ = pkcs12.load_key_and_certificates(read(p12), _password(password))
        except ValueError as exc:
            raise SigningError(f".p12 konnte nicht geöffnet werden (Passwort falsch?): {exc}") from exc
        if certificate is None or private_key is None:
            raise SigningError("Die .p12-Datei enthält kein Zertifikat mit privatem Schlüssel.")
    elif cert is not None and key is not None:
        certificate = _load_cert(read(cert))
        try:
            private_key = serialization.load_pem_private_key(read(key), _password(password))
        except (ValueError, TypeError) as exc:
            raise SigningError(f"Privater Schlüssel konnte nicht gelesen werden: {exc}") from exc
    else:
        raise SigningError("Signatur-Zertifikat fehlt: entweder --p12 oder --cert und --key angeben.")

    if certificate.public_key().public_numbers() != private_key.public_key().public_numbers():
        raise SigningError("Zertifikat und privater Schlüssel passen nicht zusammen.")
    return Signer(certificate, private_key, wwdr_cert)
