"""API-Schlüssel und verschlüsselte Geheimnisse."""

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken

KEY_PREFIX = "wk"


def generate_api_key():
    """Neuen Schlüssel erzeugen. Rückgabe: (vollständiger Schlüssel, Präfix, Hash des Geheimteils).

    Format: ``wk_<präfix>_<geheim>``. Gespeichert werden nur Präfix (zum Nachschlagen)
    und SHA-256 des Geheimteils; der Schlüssel selbst wird nur einmal angezeigt.
    """
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return f"{KEY_PREFIX}_{prefix}_{secret}", prefix, hash_secret(secret)


def hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def split_api_key(key):
    """``wk_<präfix>_<geheim>`` -> (präfix, geheim) oder None."""
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def secret_matches(secret, stored_hash):
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def random_token(nbytes=24):
    return secrets.token_urlsafe(nbytes)


class Vault:
    """Symmetrische Verschlüsselung für Geheimnisse in der Datenbank (Fernet)."""

    def __init__(self, key):
        if not key:
            raise RuntimeError("WALLET_SECRET_KEY fehlt. Erzeugen mit: python -m app generate-secret")
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @staticmethod
    def generate_key():
        return Fernet.generate_key().decode()

    def encrypt(self, text):
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token):
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Geheimnis konnte nicht entschlüsselt werden (WALLET_SECRET_KEY geändert?)") from exc
