"""FastAPI auth dependencies: current user / workspace resolution."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User, Workspace

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    creds_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise creds_exc
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise creds_exc
    return user


def get_current_workspace(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Workspace:
    workspace = (
        db.query(Workspace)
        .filter(Workspace.owner_id == user.id)
        .order_by(Workspace.created_at)
        .first()
    )
    if workspace is None:
        workspace = Workspace(name="Default Workspace", owner_id=user.id)
        db.add(workspace)
        db.commit()
        db.refresh(workspace)
    return workspace


def require_superuser(user: User = Depends(get_current_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="insufficient privileges")
    return user


def require_recent_auth(
    token: str = Depends(oauth2_scheme),
    user: User = Depends(get_current_user),
) -> User:
    """Credential-mutation guard: require a token issued within the window.

    Adding, changing, or deleting provider credentials demands a recent
    authentication so a long-lived bearer token alone cannot rotate secrets.
    """
    import time as _time

    from app.core.config import get_settings

    payload = decode_access_token(token)
    issued = payload.get("iat") if payload else None
    if not issued or _time.time() - float(issued) > get_settings().PROVIDER_REAUTH_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Re-authentication required. Please log in again before changing provider credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
