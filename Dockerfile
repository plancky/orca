# syntax=docker/dockerfile:1
#
# One image, three roles (API / Celery worker / Celery beat) that differ only by
# the compose `command:`. CPU-only — inference stays external via
# INFERENCE_BASE_URL, so no model weights, no CUDA, no google-* SDK.
# See docs/PLAN.md "Containerization & deployment" (l.786-838).

# ---------------------------------------------------------------------------
# base — pinned slim Python + hardened env + non-root user (shared downstream).
# Digest-pinned for reproducible rebuilds (python:3.12-slim, linux/amd64).
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Non-root service account (uid/gid 1000). Created in `base` so the builder and
# runtime stages agree on ownership before any COPY --chown.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

# ---------------------------------------------------------------------------
# builder — resolve + install the locked deps into /app/.venv (prod group only).
# Only pyproject.toml + uv.lock enter this layer, so the wheel-install layer
# caches independently of source churn (code edits keep dep rebuild sub-second).
# ---------------------------------------------------------------------------
FROM base AS builder

# Static, pinned uv binary — no pip bootstrap needed.
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /usr/local/bin/uv

# Deterministic resolver posture:
#  - UV_PYTHON_DOWNLOADS=0 : never fetch a managed CPython; reuse the slim
#    image interpreter so the copied venv's symlinks resolve in `runtime`.
#  - UV_LINK_MODE=copy     : materialise real files in the venv (not hardlinks
#    into the cache mount) so COPY --from=builder carries intact content.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
        --python /usr/local/bin/python3.12

# ---------------------------------------------------------------------------
# runtime — slim base + copied venv + app source. Non-root, tini as PID 1.
# No compiler/toolchain (asyncpg + pgvector ship pure-pip wheels), no weights.
# ---------------------------------------------------------------------------
FROM base AS runtime

# tini: correct SIGTERM forwarding + zombie reaping as PID 1.
# curl: the HEALTHCHECK probe. Both are the only non-venv additions.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini curl \
    && rm -rf /var/lib/apt/lists/*

# Put the venv first on PATH (its uvicorn/celery/python win) and the app on the
# import path so console scripts (which don't add cwd) resolve `backend`.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app

WORKDIR /app

# Pre-built dependency venv, then the application source — both owned by `app`.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app backend/ /app/backend/

USER app

EXPOSE 8000

# API liveness. Worker/beat containers override `command:` but keep this probe
# harmless (they simply report unhealthy on /health, which is expected — compose
# gives them their own role-appropriate checks if desired).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# tini as PID 1 so signals reach uvicorn/celery and zombies are reaped.
ENTRYPOINT ["tini", "--"]

# Default role = API (2 uvicorn workers, I/O-bound). Worker/beat override in compose.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
