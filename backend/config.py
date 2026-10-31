from functools import lru_cache
from pathlib import Path
from cryptography.fernet import Fernet

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
MIN_ACCESS_KEY_LENGTH = 16
DEFAULT_ZOHO_ACCOUNTS_URL = "https://accounts.zoho.com"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    anthropic_api_key: SecretStr
    anthropic_model: str
    access_key: SecretStr
    frontend_origin: str
    database_url: SecretStr 
    @field_validator("zoho_accounts_url")
    @classmethod
    def accounts_url_is_https(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            return DEFAULT_ZOHO_ACCOUNTS_URL
        if not v.startswith("https://"):
            raise ValueError("must start with https://")
        return v

    @field_validator("zoho_redirect_uri")
    @classmethod
    def redirect_uri_is_url(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return v

    @field_validator("token_encryption_key")
    @classmethod
    def encryption_key_is_valid(cls, v: SecretStr) -> SecretStr:
        key = v.get_secret_value().strip()
        if key:
            try:
                Fernet(key.encode())
            except ValueError:
                raise ValueError(
                    "must be a key made by Fernet.generate_key() (44 characters, ending in =)"
                ) from None
        return SecretStr(key)

    max_upload_mb: int = 10
    max_invoice_age_days: int = 365
    validation_tolerance: float = 0.01
    enable_api_docs: bool = False
    zoho_client_id: str = ""
    zoho_client_secret: SecretStr = SecretStr("")
    zoho_redirect_uri: str = ""
    zoho_accounts_url: str = DEFAULT_ZOHO_ACCOUNTS_URL
    zoho_organization_id: str = ""
    zoho_expense_account_id: str = ""
    token_encryption_key: SecretStr = SecretStr("")
    @field_validator("zoho_organization_id", "zoho_expense_account_id")
    @classmethod
    def zoho_id_is_digits(cls, v: str) -> str:
        v = v.strip()
        if v and not v.isdigit():
            raise ValueError("must be the numbers only, as shown in Zoho Books")
        return v

    token_encryption_key: SecretStr = SecretStr("")

    @property
    def zoho_configured(self) -> bool:
        return bool(
            self.zoho_client_id.strip()
            and self.zoho_client_secret.get_secret_value().strip()
            and self.zoho_redirect_uri
            and self.token_encryption_key.get_secret_value()
        )

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