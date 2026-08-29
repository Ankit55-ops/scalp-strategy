"""Security helpers: password hashing, JWT, field-level encryption at rest."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.core.config import get_settings


def derive_fernet_key(data_encryption_key: bytes | None = None) -> bytes:
    settings = get_settings()
    raw = data_encryption_key or settings.data_encryption_key_bytes
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


class Cipher:
    """Symmetric field-level encryption for secrets at rest (AES via Fernet)."""

    def __init__(self, key: bytes | None = None) -> None:
        self._fernet = Fernet(derive_fernet_key(key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Unable to decrypt secret") from exc


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    settings = get_settings()
    expire = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expire)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except JWTError:
        return None


def validate_password_strength(password: str) -> bool:
    return len(password) >= 8
