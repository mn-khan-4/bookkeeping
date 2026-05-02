"""
Main FastAPI application factory.

Entry point for the AI Bookkeeping Agent Platform backend.
Run with:  uvicorn app.main:app --reload
"""

import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import admin, exceptions, integrations, payables, reconciliation
from app.core.config import settings
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)


# ======================================================================
#  Lifespan — startup / shutdown hooks
# ======================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application lifespan events.

    Startup:  Initialise logging, future DB connections, caches, etc.
    Shutdown: Cleanly close any open resources.
    """
    # --- Startup ---
    setup_logging()
    logger.info(
        "🚀 %s v%s starting up in %s mode …",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )

    yield  # Application runs here

    # --- Shutdown ---
    logger.info("💤 %s shutting down.", settings.APP_NAME)


# ======================================================================
#  Application factory
# ======================================================================

def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""

    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "AI-powered bookkeeping automation platform. "
            "Operates entirely within Xero using an intelligent matching "
            "and reconciliation engine."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------
    # CORS Middleware
    # ------------------------------------------------------------------
    origins = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]  # Dev frontends
    if settings.ENVIRONMENT == "production":
        origins = []  # Lock down to specific origins in production

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    application.include_router(
        integrations.router,
        prefix=settings.API_V1_PREFIX,
    )
    application.include_router(
        reconciliation.router,
        prefix=settings.API_V1_PREFIX,
    )
    application.include_router(
        exceptions.router,
        prefix=settings.API_V1_PREFIX,
    )
    application.include_router(
        payables.router,
        prefix=settings.API_V1_PREFIX,
    )
    application.include_router(
        admin.router,
        prefix=settings.API_V1_PREFIX,
    )

    # ------------------------------------------------------------------
    # Frontend (static dashboard)
    # ------------------------------------------------------------------
    frontend_dir = Path(__file__).resolve().parents[2] / "Frontend"
    if frontend_dir.exists():
        application.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="frontend")

        @application.get("/dashboard", include_in_schema=False)
        async def dashboard() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    # ------------------------------------------------------------------
    # Root & platform health endpoints
    # ------------------------------------------------------------------

    @application.get("/", tags=["Platform"], summary="Platform root")
    async def root() -> JSONResponse:
        return JSONResponse(
            content={
                "platform": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "environment": settings.ENVIRONMENT,
                "docs": "/docs",
                "status": "online",
            }
        )

    @application.get("/health", tags=["Platform"], summary="Platform health check")
    async def health() -> JSONResponse:
        return JSONResponse(
            content={
                "status": "ok",
                "service": settings.APP_NAME,
                "version": settings.APP_VERSION,
            }
        )

    return application


# ======================================================================
#  App instance (used by uvicorn)
# ======================================================================

app = create_application()
