from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
MIN_ACCESS_KEY_LENGTH = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    anthropic_api_key: SecretStr
    anthropic_model: str
    access_key: SecretStr
    frontend_origin: str
    database_url: SecretStr 
    max_upload_mb: int = 10
    max_invoice_age_days: int = 365
    validation_tolerance: float = 0.01
    enable_api_docs: bool = False

    @field_validator("anthropic_api_key")
    @classmethod
    def key_not_blank(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("database_url")
    @classmethod
    def database_url_usable(cls, v: SecretStr) -> SecretStr:
        url = v.get_secret_value().strip()
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
        if not url.startswith(("postgresql+psycopg://", "sqlite://")):
            raise ValueError("must be a PostgreSQL address starting with postgresql://")
        return SecretStr(url)

    @field_validator("access_key")
    @classmethod
    def access_key_long_enough(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value().strip()) < MIN_ACCESS_KEY_LENGTH:
            raise ValueError(f"must be at least {MIN_ACCESS_KEY_LENGTH} characters")
        return v

    @field_validator("anthropic_model")
    @classmethod
    def model_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("frontend_origin")
    @classmethod
    def origin_is_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("max_upload_mb", "max_invoice_age_days")
    @classmethod
    def at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @field_validator("validation_tolerance")
    @classmethod
    def not_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must not be negative")
        return v


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {str(err['loc'][0]).upper()}: {err['msg']}" for err in exc.errors()
        )
        raise SystemExit(
            "\nDocFlow cannot start: invalid configuration.\n"
            f"{problems}\n"
            f"Set these in {ENV_FILE} (copy .env.example) or as environment variables.\n"
        )