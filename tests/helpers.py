"""Test helpers: a fake WWDR CA and pass certificate (self-signed)."""

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

PTI = "pass.com.example.test"
TEAM = "ABCDE12345"


def _cert(subject, issuer, public_key, signing_key, ca):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(public_key).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(signing_key, hashes.SHA256()))


def make_credentials(password=b"secret", pti=PTI, team=TEAM):
    """Returns dict with wwdr (PEM), p12, cert (PEM), key (PEM) bytes."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test WWDR")])
    ca = _cert(ca_name, ca_name, ca_key.public_key(), ca_key, True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.USER_ID, pti),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Pass Type ID: {pti}"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, team),
    ])
    cert = _cert(subject, ca_name, key.public_key(), ca_key, False)
    enc = serialization.BestAvailableEncryption(password)
    return {
        "wwdr": ca.public_bytes(serialization.Encoding.PEM),
        "wwdr_der": ca.public_bytes(serialization.Encoding.DER),
        "cert": cert.public_bytes(serialization.Encoding.PEM),
        "key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc),
        "p12": pkcs12.serialize_key_and_certificates(b"pass", key, cert, None, enc),
        "password": password.decode(),
    }
