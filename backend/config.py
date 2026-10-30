from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    anthropic_api_key: SecretStr
    anthropic_model: str
    frontend_origin: str
    database_url: SecretStr | None = None

    @field_validator("anthropic_api_key")
    @classmethod
    def key_not_blank(cls, v: SecretStr) -> SecretStr:
        if not v.get_secret_value().strip():
            raise ValueError("must not be empty")
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