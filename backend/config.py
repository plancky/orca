import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/workspace_orchestrator"
    )

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # Inference (external, CPU-only image)
    INFERENCE_BASE_URL: str = Field(default="http://localhost:11434")
    CHAT_MODEL: str = Field(default="llama3")
    EMBED_MODEL: str = Field(default="nomic-embed-text")
    RERANK_MODEL: str = Field(default="bge-reranker-v2-m3")
    EMBED_DIM: int = 1024

    # Embedding mode
    EMBED_MODE: str = Field(default="fake")  # "fake" | "real"
    RERANK_ENABLED: bool = False

    # Sync
    SYNC_BEAT_MINUTES: int = 15

    # Auth
    SECRET_KEY: str = Field(default="")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days
    FIRST_SUPERUSER_EMAIL: str = Field(default="admin@example.com")
    FIRST_SUPERUSER_PASSWORD: str = Field(default="")

    # App
    API_V1_STR: str = "/api/v1"
    DEFAULT_TZ: str = "America/New_York"
    PROVIDER: str = "mock"  # "mock" | "google"

    # Rate limiting
    RATE_LIMIT_PER_USER_PER_HOUR: int = 100

    @model_validator(mode="after")
    def check_secret_key(self) -> "Settings":
        if not self.SECRET_KEY:
            if not os.getenv("TESTING"):
                raise ValueError(
                    "SECRET_KEY must be set. Generate: "
                    'python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
        return self


settings = Settings()
