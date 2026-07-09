from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import (
    routes_auth,
    routes_conversations,
    routes_login,
    routes_query,
    routes_sync,
    routes_tasks,
    routes_users,
    ws,
)
from backend.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic Google Workspace Orchestrator",
        description=(
            "Classifies intent, plans a DAG, fans out to Gmail/GCal/Drive "
            "agents, hybrid pgvector search, synthesizes answers."
        ),
        version="1.0.0",
        contact={"name": "Support"},
        servers=[{"url": "/", "description": "Default"}],
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.API_V1_STR
    app.include_router(routes_login.router, prefix=prefix)
    app.include_router(routes_users.router, prefix=prefix)
    app.include_router(routes_auth.router, prefix=prefix)
    app.include_router(routes_query.router, prefix=prefix)
    app.include_router(routes_tasks.router, prefix=prefix)
    app.include_router(routes_sync.router, prefix=prefix)
    app.include_router(routes_conversations.router, prefix=prefix)
    app.include_router(ws.router)  # WS has its own path /ws/query

    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
