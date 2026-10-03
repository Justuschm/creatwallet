"""Push an Apple (APNs) für Pass-Updates.

Wallet-Pässe werden mit dem Zertifikat der Pass Type ID an APNs geschickt (TLS-Client-
Zertifikat, HTTP/2). Die Nachricht ist leer: Sie weckt nur das Gerät, das sich die neue
Version dann selbst beim Web-Service abholt.
"""

import os
import ssl
import tempfile
import threading

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

OK, GONE, RETRY, ERROR = "ok", "gone", "retry", "error"


class ApnsPusher:
    def __init__(self, store, host="api.push.apple.com", push_type=""):
        self.store = store
        self.base = f"https://{host}/3/device/"
        self.push_type = push_type
        self._clients = {}
        self._lock = threading.Lock()

    def _ssl_context(self, pti):
        p12, password = self.store.load(pti)
        key, cert, extra = pkcs12.load_key_and_certificates(p12, password.encode() if password else None)
        ctx = ssl.create_default_context()
        # load_cert_chain liest nur Dateien: kurz in ein privates Verzeichnis schreiben und sofort löschen.
        with tempfile.TemporaryDirectory() as d:
            os.chmod(d, 0o700)
            certfile, keyfile = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
            with open(certfile, "wb") as fh:
                fh.write(cert.public_bytes(serialization.Encoding.PEM))
                for c in extra or []:
                    fh.write(c.public_bytes(serialization.Encoding.PEM))
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
            ctx.load_cert_chain(certfile, keyfile)
        return ctx

    def _client(self, pti):
        with self._lock:
            client = self._clients.get(pti)
            if client is None:
                client = httpx.Client(http2=True, verify=self._ssl_context(pti), timeout=15)
                self._clients[pti] = client
            return client

    def send(self, certificate, push_token):
        """Rückgabe: ok | gone (Gerät abgemeldet) | retry (später erneut) | error (dauerhaft)."""
        pti = certificate.pass_type_identifier
        headers = {"apns-topic": pti}
        if self.push_type:
            headers["apns-push-type"] = self.push_type
        try:
            r = self._client(pti).post(self.base + push_token, content=b"{}", headers=headers)
        except httpx.HTTPError as exc:
            return RETRY, f"Verbindung zu APNs: {exc}"
        return classify(r.status_code, _reason(r))


def _reason(response):
    try:
        return response.json().get("reason", "")
    except ValueError:
        return response.text[:200]


def classify(status, reason=""):
    if status == 200:
        return OK, ""
    if status == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
        return GONE, reason
    if status == 429 or status >= 500:
        return RETRY, f"{status} {reason}"
    return ERROR, f"{status} {reason}"
