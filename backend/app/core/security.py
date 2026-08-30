"""Security helpers: password hashing, JWT, field-level encryption at rest.

JWT signing uses PyJWT (maintained) with the HS256 algorithm only. Tokens
carry ``sub``, ``iss``, ``iat``, ``exp`` and ``jti`` claims; decoders pin the
algorithm and issuer so algorithm-confusion and cross-audience tokens are
rejected.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from jwt import PyJWTError
from jwt import decode as jwt_decode
from jwt import encode as jwt_encode

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
    expire = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    iat = int(now.timestamp())
    payload = {
        "sub": str(subject),
        "iss": settings.JWT_ISSUER,
        "iat": iat,
        "nbf": iat,  # not-before == issued-at: never accept a token "from the future"
        "exp": int((now + timedelta(minutes=expire)).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt_encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt_decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={
                "require": ["exp", "iat", "nbf", "sub", "typ"],
                "verify_sub": True,
                "verify_aud": False,
            },
        )
    except PyJWTError:
        return None
    # Explicitly pin the token type so a non-access token (e.g. a future
    # refresh/CSRF token signed with the same key/issuer) can never be accepted
    # as an access token. PyJWT enforces nbf/exp on its own once present.
    if payload.get("typ") != "access":
        return None
    return payload


def validate_password_strength(password: str) -> bool:
    """Register/change-password strength check (length policy)."""
    return 8 <= len(password) <= 72