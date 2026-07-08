import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/workspace_orchestrator"
    )
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # --- Inference/embeddings: Google Gemini (AI Studio) via OpenAI-compat layer ---
    # empty OK at import; required only when EMBED_MODE=real / real chat.
    GEMINI_STUDIO_API_KEY: str = Field(default="")
    # trailing slash; append 'chat/completions' & 'embeddings' (NO /v1).
    INFERENCE_BASE_URL: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    # current free-tier flash; env-overridable.
    CHAT_MODEL: str = Field(default="gemini-2.5-flash")
    # GA embedding model; env-overridable (newer: gemini-embedding-2* if current).
    EMBED_MODEL: str = Field(default="gemini-embedding-001")
    RERANK_MODEL: str = Field(default="bge-reranker-v2-m3")
    EMBED_DIM: int = 1024
    # BGE prefix OFF for Gemini (symmetric embed).
    EMBED_QUERY_PREFIX: str = Field(default="")
    EMBED_MODE: str = Field(default="fake")  # "fake" | "real"
    RERANK_ENABLED: bool = False
    GEMINI_MAX_CONCURRENCY: int = 4
    GEMINI_EMBED_BATCH_SIZE: int = 32
    GEMINI_MAX_RETRIES: int = 5

    SYNC_BEAT_MINUTES: int = 15

    SECRET_KEY: str = Field(default="")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FIRST_SUPERUSER_EMAIL: str = Field(default="admin@example.com")
    FIRST_SUPERUSER_PASSWORD: str = Field(default="")

    API_V1_STR: str = "/api/v1"
    DEFAULT_TZ: str = "America/New_York"
    PROVIDER: str = "mock"
    RATE_LIMIT_PER_USER_PER_HOUR: int = 100

    @model_validator(mode="after")
    def check_secret_key(self) -> "Settings":
        if not self.SECRET_KEY and not os.getenv("TESTING"):
            raise ValueError(
                "SECRET_KEY must be set. Generate: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return self


settings = Settings()
