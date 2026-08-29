from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    BACKTEST_ASYNC: bool = False

    ENABLE_CSRF: bool = False

    DATA_ENCRYPTION_KEY: str = ""

    @property
    def data_encryption_key_bytes(self) -> bytes:
        if self.DATA_ENCRYPTION_KEY:
            import base64

            return base64.b64decode(self.DATA_ENCRYPTION_KEY)
        import hashlib

        return hashlib.sha256(self.SECRET_KEY.encode()).digest()


@lru_cache
def get_settings() -> Settings:
    return Settings()
