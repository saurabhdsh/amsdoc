"""Runtime configuration. Secrets come from the environment, never from the browser."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = API_ROOT / "data"
DEFAULT_SQLITE = DATA_DIR / "ams.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    secret_key: str = "local-dev-only-change-me"
    database_url: str = f"sqlite:///{DEFAULT_SQLITE}"
    redis_url: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    access_token_hours: int = 12
    segregation_of_duties: bool = False

    servicenow_instance_url: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    ollama_base_url: str = ""
    ollama_model: str = "llama3.1"
    bedrock_enabled: bool = False
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:5173/auth/callback"

    otel_exporter_otlp_endpoint: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    bootstrap_admin_name: str = "Platform Administrator"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def assert_production_safe(self) -> None:
        if self.environment == "production" and (
            not self.secret_key or self.secret_key.startswith("local-dev")
        ):
            raise RuntimeError("SECRET_KEY must be set to a unique value when ENVIRONMENT=production")


def get_settings() -> Settings:
    return Settings()
