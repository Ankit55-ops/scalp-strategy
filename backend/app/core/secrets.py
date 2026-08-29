"""Provider credential encryption helpers.

Provider API keys are stored server-side only and are always encrypted at rest
with :class:`cryptography.fernet.Fernet`. The encryption key is derived either
from ``DATA_ENCRYPTION_KEY`` (base64) or a hash of ``SECRET_KEY``.
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet


def _fernet():
    from app.core.config import get_settings

    key = get_settings().data_encryption_key_bytes
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")