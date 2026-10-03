"""Webhooks an Firmen: Zieladresse prüfen, Nachricht signieren, zustellen."""

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

import httpx

SIGNATURE_HEADER = "Wallet-Signature"


class UnsafeUrl(ValueError):
    pass


def sign(secret, body_bytes, timestamp=None):
    """``t=<unix>,v1=<hmac>`` - die Firma prüft HMAC-SHA256 über ``"<t>.<body>"`` mit ihrem Geheimnis."""
    t = int(timestamp or time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body_bytes, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret, body_bytes, header, tolerance=300):
    """Gegenstück für Firmen (und Tests)."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t = int(parts.get("t", 0))
    if abs(time.time() - t) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, body_bytes, t), header)


def check_url(url, allow_insecure=False):
    """Nur https (lokal zum Testen optional http) und keine internen Adressen (Schutz vor SSRF)."""
    parts = urlsplit(url)
    if parts.scheme != "https" and not (allow_insecure and parts.scheme == "http"):
        raise UnsafeUrl("Webhook-URL muss mit https:// beginnen.")
    if not parts.hostname:
        raise UnsafeUrl("Webhook-URL ohne Host.")
    if allow_insecure:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"Host {parts.hostname} nicht auflösbar.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeUrl(f"Host {parts.hostname} zeigt auf eine interne Adresse ({ip}).")


def deliver(client, url, secret, body, allow_insecure=False):
    """Rückgabe (erfolgreich, Fehlertext)."""
    try:
        check_url(url, allow_insecure)
    except UnsafeUrl as exc:
        return False, str(exc)
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, raw),
               "User-Agent": "Wallet-Pass-Platform-Webhooks/1"}
    try:
        r = client.post(url, content=raw, headers=headers, follow_redirects=False)
    except httpx.HTTPError as exc:
        return False, f"Zustellung fehlgeschlagen: {exc}"
    if 200 <= r.status_code < 300:
        return True, ""
    return False, f"Antwort {r.status_code}"
