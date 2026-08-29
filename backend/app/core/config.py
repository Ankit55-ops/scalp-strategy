import base64
import json
import logging
import os
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("fxscalper.config")

# Values that are never acceptable as a real SECRET_KEY regardless of env.
WEAK_SECRETS = frozenset(
    {
        "insecure-development-secret-change-me",
        "change-me",
        "change-me-in-production",
        "ci-secret-key",
    }
)

# Environments considered non-production: warnings only, never hard failures.
DEV_ENVS = frozenset({"", "development", "dev", "test", "testing"})

SAFE_SYMBOL_RE_STR = r"^[A-Z0-9]{1,16}$"
ALLOWED_TIMEFRAMES = frozenset({"M1", "M5", "M15", "M30", "H1", "H4", "D1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    SECRET_KEY: str = "insecure-development-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "fxscalper-lab"

    DATABASE_URL: str = (
        "postgresql+psycopg://fxscalper:change-me@localhost:5432/fxscalper"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    LLM_PROVIDER: str = "mock"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"

    MARKET_DATA_PROVIDER: str = "mock"
    BROKER_PROVIDER: str = "simulated"

    # -- real market data providers (OANDA v20 / Twelve Data) -------------
    OANDA_API_KEY: str = ""
    OANDA_ACCOUNT_ID: str = ""
    OANDA_ENV: str = "practice"  # practice | live
    OANDA_BASE_URL: str = "https://api-fxpractice.oanda.com"

    TWELVEDATA_API_KEY: str = ""
    TWELVEDATA_BASE_URL: str = "https://api.twelvedata.com"
    TWELVEDATA_USE_WEBSOCKET: bool = False  # requires a plan with WS access

    PROVIDER_TIMEOUT_SECONDS: float = 15.0
    PROVIDER_MAX_RETRIES: int = 2
    STALE_QUOTE_THRESHOLD_SECONDS: int = 30
    FEED_HEALTH_INTERVAL_SECONDS: int = 15

    # -- real-time ingestion (tick polling -> candle aggregation) --------
    MARKET_DATA_INGESTION_ENABLED: bool = True
    DATA_INGESTION_POLL_INTERVAL_SECONDS: float = 2.0
    DATA_INGESTION_TIMEFRAMES: str = "M1,M5,M15,H1"
    DATA_INGESTION_TICK_PERSIST_EVERY_N: int = 25

    # -- practice / live execution ---------------------------------------
    LIVE_TRADING_ENABLED: bool = False  # master kill for any real execution
    BROKER_PRACTICE_DRY_RUN: bool = True

    BACKTEST_ASYNC: bool = False

    ENABLE_CSRF: bool = False

    DATA_ENCRYPTION_KEY: str = ""

    # -- security controls ------------------------------------------------
    CORS_ORIGINS: str = '["http://localhost:3000", "http://127.0.0.1:3000"]'
    TRUST_PROXY_HEADERS: bool = False
    RATE_LIMIT_DEFAULT: int = 120
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    AUTH_LOGIN_MAX_ATTEMPTS_PER_WINDOW: int = 10
    AUTH_LOGIN_WINDOW_SECONDS: int = 600
    ENABLE_HSTS: bool = False

    UPLOAD_MAX_BYTES: int = 8 * 1024 * 1024
    MAX_EXPRESSION_LENGTH: int = 2048
    MAX_EXPRESSION_TOKENS: int = 512
    MAX_EXPRESSION_DEPTH: int = 40

    MAX_CONCURRENT_BACKTESTS_PER_WORKSPACE: int = 1
    MAX_CONCURRENT_WS_PER_USER: int = 4

    PAPER_MAX_LEVERAGE: float = 20.0
    PAPER_MIN_BALANCE: float = 1000.0
    PAPER_MAX_BALANCE: float = 10_000_000.0

    # -- validation -------------------------------------------------------
    @field_validator("JWT_ALGORITHM")
    @classmethod
    def _jwt_algorithm_allow_listed(cls, v: str) -> str:
        if v not in {"HS256"}:
            raise ValueError(
                f"JWT_ALGORITHM={v!r} is not allow-listed; only HS256 is supported"
            )
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_minimum(cls, v: str) -> str:
        if not v:
            raise ValueError("SECRET_KEY must not be empty")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _cors_origins_valid_json(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CORS_ORIGINS must be a JSON array of origins: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(o, str) for o in parsed):
            raise ValueError("CORS_ORIGINS must be a JSON array of origin strings")
        return v

    @property
    def cors_origins(self) -> list[str]:
        return json.loads(self.CORS_ORIGINS)

    @property
    def is_production(self) -> bool:
        return (self.APP_ENV or "development").lower() == "production"

    @property
    def data_encryption_key_bytes(self) -> bytes:
        if self.DATA_ENCRYPTION_KEY:
            return base64.b64decode(self.DATA_ENCRYPTION_KEY)
        import hashlib

        return hashlib.sha256(self.SECRET_KEY.encode()).digest()

    @model_validator(mode="after")
    def _verify_security_config(self) -> "Settings":
        env = (self.APP_ENV or "development").lower()
        # Fail-closed outside development/test environments.
        if env not in DEV_ENVS:
            if self.SECRET_KEY in WEAK_SECRETS or len(self.SECRET_KEY) < 32:
                raise ValueError(
                    "SECRET_KEY must be a random value of at least 32 characters in "
                    f"APP_ENV={self.APP_ENV!r}. Set SECRET_KEY (run: openssl rand -hex 32)."
                )
            if self.LLM_PROVIDER.lower() == "llm" and not self.LLM_API_KEY:
                raise ValueError("LLM_API_KEY is required when LLM_PROVIDER=llm")
            if not self.DATA_ENCRYPTION_KEY:
                raise ValueError(
                    "DATA_ENCRYPTION_KEY is required for encryption at rest in "
                    f"APP_ENV={self.APP_ENV!r}. Generate with: "
                    "python -c \"import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
                )
        else:
            if self.SECRET_KEY in WEAK_SECRETS or len(self.SECRET_KEY) < 16:
                logger.warning(
                    "SECRET_KEY is weak or a known placeholder. For any deployment or "
                    "shared demo set a strong SECRET_KEY (run: openssl rand -hex 32)."
                )

        # DATA_ENCRYPTION_KEY, when supplied, must be a 32-byte base64 value.
        if self.DATA_ENCRYPTION_KEY:
            try:
                raw = base64.b64decode(self.DATA_ENCRYPTION_KEY)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    "DATA_ENCRYPTION_KEY is not valid base64"
                ) from exc
            if len(raw) != 32:
                raise ValueError(
                    "DATA_ENCRYPTION_KEY must decode to exactly 32 bytes "
                    "(e.g. base64 of os.urandom(32)); got "
                    f"{len(raw)} bytes"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def default_data_encryption_key() -> str:
    """Generate a fresh 32-byte url-safe base64 key (for provisioning only)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()