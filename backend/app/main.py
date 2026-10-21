from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    accounts,
    ai,
    analytics,
    auth,
    campaigns,
    content,
    discovery,
    health,
    inbox,
    leads,
    lists,
    public,
    sends,
    settings,
    suppressions,
)
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, register_secret
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware

log = logging.getLogger("app")


def _bootstrap_admin() -> None:
    from app.core.security import hash_password
    from app.db.session import session_scope
    from app.services.settings import get_password_hash, set_password_hash

    s = get_settings()
    if not s.admin_password:
        return
    try:
        with session_scope() as db:
            if get_password_hash(db) is None:
                set_password_hash(db, hash_password(s.admin_password))
                log.info("Admin password initialised from ADMIN_PASSWORD")
    except Exception:  # noqa: BLE001 - DB may not be migrated yet
        log.warning("Could not bootstrap admin password (have you run migrations?)")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    for secret in (
        s.secret_key,
        s.encryption_key,
        s.admin_password,
        s.ai_api_key,
        s.google_client_secret,
        s.microsoft_client_secret,
        s.apollo_api_key,
        s.google_search_api_key,
        s.gemini_api_key,
    ):
        register_secret(secret)
    try:
        loopback = ipaddress.ip_address(s.app_host).is_loopback
    except ValueError:
        loopback = s.app_host == "localhost"
    if not loopback:
        log.warning("Bound to %s (not localhost). Authentication is enforced; use HTTPS and COOKIE_SECURE=true.", s.app_host)
    _bootstrap_admin()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="Personal Outreach API",
        version="1.0.0",
        lifespan=lifespan,
        # Interactive docs are for development; hidden in production deployments.
        docs_url=None if s.app_env == "production" else "/api/docs",
        openapi_url=None if s.app_env == "production" else "/api/openapi.json",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=s.max_request_bytes, max_upload_bytes=s.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    for r in (
        health,
        auth,
        leads,
        lists,
        accounts,
        content,
        campaigns,
        inbox,
        suppressions,
        settings,
        analytics,
        ai,
        sends,
        discovery,
        public,
    ):
        app.include_router(r.router)

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"error": {"code": "not_found", "message": "Unknown API route", "details": None}}, status_code=404)

    dist = s.frontend_dist
    if dist.exists() and (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
