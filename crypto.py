"""Encryption helpers for protecting API credentials at rest.

Uses Fernet symmetric encryption with a key derived from SESSION_SECRET.
Values are encrypted before writing to companies.json and decrypted on read.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import settings

# Marker prefix so we know a value is encrypted (vs legacy plaintext)
_ENCRYPTED_PREFIX = "enc:"


def _get_fernet() -> Fernet:
    """Derive a Fernet key from SESSION_SECRET using SHA-256."""
    key_bytes = hashlib.sha256(settings.session_secret.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns prefixed ciphertext."""
    if not plaintext:
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return _ENCRYPTED_PREFIX + token.decode("utf-8")


def decrypt_value(stored: str) -> str:
    """Decrypt a stored value. Handles both encrypted and legacy plaintext."""
    if not stored:
        return stored
    if not stored.startswith(_ENCRYPTED_PREFIX):
        # Legacy plaintext — return as-is (will be encrypted on next save)
        return stored
    try:
        token = stored[len(_ENCRYPTED_PREFIX):].encode("utf-8")
        return _get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken:
        # SESSION_SECRET changed or data corrupted — return empty
        return ""
