from fastapi import APIRouter, Depends, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.middleware import client_ip
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User, Workspace
from app.schemas.auth import Token, UserCreate, UserLogin, UserOut, WorkshopCreate

router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Pre-computed hash so non-existent-emails still cost a bcrypt verify.
_DUMMY_HASH = pwd_context.hash("timing-equalization-dummy-password")


class LoginFailureLimiter:
    """Consecutive-failure lockout keyed on (email, IP).

    A successful login resets the counter; only failed attempts accumulate.
    Sliding 600s window by default (AUTH_LOGIN_* settings).
    """

    def __init__(self) -> None:
        self._max = get_settings().AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW
        self._window = get_settings().AUTH_LOGIN_WINDOW_SECONDS
        self._attempts: dict[str, list[float]] = {}

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def hit(self, key: str) -> None:
        import time

        now = time.time()
        bucket = self._attempts.setdefault(key, [])
        bucket[:] = [t for t in bucket if t > now - self._window]
        bucket.append(now)


_lockout = LoginFailureLimiter()


def _login_key(email: str, request: Request) -> str:
    return f"login:{email}:{client_ip(request)}"


def _check_lockout(key: str) -> None:
    import time

    settings = get_settings()
    recent = [
        t
        for t in _lockout._attempts.get(key, [])
        if t > time.time() - settings.AUTH_LOGIN_WINDOW_SECONDS
    ]
    if len(recent) >= settings.AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW:
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(settings.AUTH_LOGIN_WINDOW_SECONDS)},
        )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    email = payload.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="email already registered")
    user = User(
        email=email,
        hashed_password=pwd_context.hash(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    default_ws = Workspace(name="Default Workspace", owner_id=user.id)
    db.add(default_ws)
    db.commit()
    return user


@router.post("/login", response_model=Token)
def login(payload: UserLogin, request: Request, db: Session = Depends(get_db)) -> Token:
    email = payload.email.strip().lower()
    key = _login_key(email, request)
    _check_lockout(key)
    user = db.query(User).filter(User.email == email).first()
    stored_hash = user.hashed_password if user else _DUMMY_HASH
    ok = pwd_context.verify(payload.password, stored_hash)
    if not user or not ok:
        _lockout.hit(key)
        if user is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        _check_lockout(key)
        raise HTTPException(status_code=401, detail="invalid credentials")
    _lockout.reset(key)
    token = create_access_token(subject=user.id)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/workspaces", response_model=dict)
def create_workspace(
    payload: WorkshopCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ws = Workspace(name=payload.name, owner_id=user.id)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return {"id": ws.id, "name": ws.name}