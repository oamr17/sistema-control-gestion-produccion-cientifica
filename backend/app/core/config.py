from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    demo_mode: bool = False
    database_url: str = "postgresql+psycopg://postgres:postgres@postgres:5432/science_faculty"
    migration_database_url: str | None = None
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    cors_origins: list[str] = ["http://localhost:3000"]
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "evidence"
    evidence_allowed_extensions: list[str] = [".pdf"]
    evidence_allowed_mime_types: list[str] = ["application/pdf"]
    evidence_max_bytes: int = 10 * 1024 * 1024
    n8n_webhook_url: str | None = None
    ingest_api_key: str = "change-me-import-key"
    dropbox_client_id: str | None = None
    dropbox_client_secret: str | None = None
    dropbox_refresh_token: str | None = None
    import_pdf_max_concurrency: int = 3
    import_pdf_max_retries: int = 2
    seed_demo_data: bool = False

    @field_validator("demo_mode", mode="before")
    @classmethod
    def parse_explicit_demo_mode(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        return value == "true"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
