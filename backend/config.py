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

    # --- Pluggable chat-inference provider (Adapter pattern) ---
    # "auto" -> Modal/Qwen when LLM_BASE_URL set, else Gemini; or force by name.
    LLM_PROVIDER: str = Field(default="auto")
    # Modal endpoint incl. version segment, e.g. https://<app>.modal.direct/v1
    LLM_BASE_URL: str = Field(default="")
    # OpenAI SDK needs a non-empty key; Modal auth rides on headers.
    LLM_API_KEY: str = Field(default="unused")
    LLM_MODEL: str = Field(default="Qwen/Qwen3.6-35B-A3B")
    LLM_MAX_TOKENS: int = Field(default=2048)
    # "none" keeps Qwen3 JSON-mode output free of think traces.
    LLM_REASONING_EFFORT: str = Field(default="none")
    # sent as Modal-Key / Modal-Secret headers on every request.
    MODAL_PROXY_TOKEN_ID: str = Field(default="")
    MODAL_PROXY_TOKEN_SECRET: str = Field(default="")

    # --- Embeddings: Modal-hosted BGE service (Adapter pattern) ---
    # Separate service from the chat LLM; reuses the same MODAL_PROXY_TOKEN_* pair.
    # Set (with EMBED_MODE=real) to route real embeddings to the BGE /embed API.
    EMBEDDER_BASE_URL: str = Field(default="")
    # BGE retrieval instruction for the QUERY side; documents embed with None.
    EMBEDDER_QUERY_INSTRUCTION: str = Field(
        default="Represent this sentence for searching relevant passages:"
    )

    SYNC_BEAT_MINUTES: int = 15

    SECRET_KEY: str = Field(default="")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8
    FIRST_SUPERUSER_EMAIL: str = Field(default="admin@example.com")
    FIRST_SUPERUSER_PASSWORD: str = Field(default="")

    API_V1_STR: str = "/api/v1"
    DEFAULT_TZ: str = "America/New_York"
    PROVIDER: str = "google"
    RATE_LIMIT_PER_USER_PER_HOUR: int = 100

    # --- Phase 2: Google Workspace OAuth + sync (PROVIDER=google) ---
    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    GOOGLE_REDIRECT_URI: str = Field(
        default="http://localhost:5173/api/v1/auth/callback"
    )
    GOOGLE_AUTH_URI: str = Field(default="https://accounts.google.com/o/oauth2/auth")
    GOOGLE_TOKEN_URI: str = Field(default="https://oauth2.googleapis.com/token")
    # Space-separated at rest (env-safe); split on whitespace at use.
    GOOGLE_SCOPES: str = Field(
        default=(
            "openid email "
            "https://www.googleapis.com/auth/userinfo.email "
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/calendar.events "
            "https://www.googleapis.com/auth/drive.readonly "
            "https://www.googleapis.com/auth/drive.file"
        )
    )
    # Fernet key for encrypting Google tokens at rest; required when PROVIDER=google.
    TOKEN_ENCRYPTION_KEY: str = Field(default="")
    # Writes are simulated unless explicitly disabled — safe by default.
    DRY_RUN_WRITES: bool = Field(default=True)
    SYNC_PAGE_SIZE: int = Field(default=100)
    # First (cursor-less) full sync window for Gmail/Drive; later passes are
    # incremental (syncToken/historyId/pageToken) and ignore this.
    SYNC_LOOKBACK_DAYS: int = Field(default=7)
    GMAIL_BATCH_SIZE: int = Field(default=50)
    GOOGLE_UNITS_PER_SEC: int = Field(default=250)
    # SPA origin the OAuth callback redirects back to (hands off the JWT in the
    # URL fragment). Local dev = the Vite dev server; set to the deployed SPA.
    FRONTEND_URL: str = Field(default="http://localhost:5173")

    @model_validator(mode="after")
    def check_secret_key(self) -> "Settings":
        if not self.SECRET_KEY and not os.getenv("TESTING"):
            raise ValueError(
                "SECRET_KEY must be set. Generate: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return self


settings = Settings()
