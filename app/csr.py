"""Zertifikatsanfrage (CSR) für Apple ohne Mac und Schlüsselbund.

1. ``create_request``: Schlüssel und CSR erzeugen, Schlüssel verschlüsselt speichern.
2. CSR bei Apple hochladen (Pass Type ID -> Create Certificate), ``pass.cer`` herunterladen.
3. ``complete_request``: ``pass.cer`` hochladen - passt es zum Schlüssel, wird daraus ein
   Zertifikat im Zertifikatsspeicher (Standard oder eigenes der Firma).
"""

import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from .certs import CertStoreError, import_certificate
from .models import CertificateRequest


def create_request(session, vault, label, owner_tenant_id=None, email=""):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, (label or "Wallet Pass")[:64])]
    if email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email[:128]))
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name(attrs)).sign(key, hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    req = CertificateRequest(owner_tenant_id=owner_tenant_id, label=label[:200], key_enc=vault.encrypt(key_pem),
                             csr_pem=csr.public_bytes(serialization.Encoding.PEM).decode())
    session.add(req)
    session.flush()
    return req


def complete_request(session, vault, store, wwdr_path, req, cer_bytes):
    if req.status != "open":
        raise CertStoreError("Diese Anfrage ist bereits abgeschlossen.")
    try:
        cert = (x509.load_pem_x509_certificate(cer_bytes) if b"-----BEGIN" in cer_bytes
                else x509.load_der_x509_certificate(cer_bytes))
    except ValueError as exc:
        raise CertStoreError("Das ist keine Zertifikatsdatei von Apple (.cer).") from exc
    key = serialization.load_pem_private_key(vault.decrypt(req.key_enc).encode(), None)
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise CertStoreError("Das Zertifikat gehört zu einer anderen Anfrage (Schlüssel passt nicht).")
    password = secrets.token_urlsafe(24)
    p12 = pkcs12.serialize_key_and_certificates(b"wallet", key, cert, None,
                                                serialization.BestAvailableEncryption(password.encode()))
    result = import_certificate(session, store, p12, password, wwdr_path, owner_tenant_id=req.owner_tenant_id)
    req.status = "completed"
    req.key_enc = ""  # Schlüssel liegt jetzt (verschlüsselt) im Zertifikatsspeicher
    return result
