from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.admin_routes import router as admin_router
from app.client_ai import router as client_ai_router
from app.client_update import router as client_update_router
from app.config import SERVER_DIR, get_settings
from app.provider_admin import router as provider_admin_router
from app.provider_manager import provider_store
from app.schemas import HealthResponse, PublicConfigResponse, VersionResponse

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

# update_server.sh creates a cryptographically random value. The deterministic fallback
# keeps the public API available before initialization, while admin login remains disabled.
session_secret = settings.admin_session_secret or hashlib.sha256(
    f"{settings.app_dir}:{settings.service_name}:admin-not-initialized".encode()
).hexdigest()

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="fdex_admin_session",
    max_age=settings.admin_session_hours * 3600,
    same_site="lax",
    https_only=settings.admin_cookie_secure,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origin_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(SERVER_DIR) / "app" / "static"
release_dir = Path(settings.release_cache_dir)
generated_dir = Path(settings.app_dir) / "server" / "data" / "generated"
release_dir.mkdir(parents=True, exist_ok=True)
generated_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/downloads", StaticFiles(directory=release_dir), name="downloads")
app.mount("/generated", StaticFiles(directory=generated_dir), name="generated")
app.include_router(admin_router)
app.include_router(provider_admin_router)
app.include_router(client_ai_router)
app.include_router(client_update_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/admin", status_code=302)


@app.get(f"{settings.api_prefix}/info")
def info() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": f"{settings.public_base_url}/docs",
        "health": f"{settings.public_base_url}{settings.api_prefix}/health",
        "admin": f"{settings.public_base_url}/admin",
    }


@app.get(f"{settings.api_prefix}/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        time=datetime.now(timezone.utc),
    )


@app.get(f"{settings.api_prefix}/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@app.get(f"{settings.api_prefix}/public-config", response_model=PublicConfigResponse)
def public_config() -> PublicConfigResponse:
    providers = provider_store().list(enabled_only=True)
    primary = providers[0] if providers else None
    return PublicConfigResponse(
        service=settings.app_name,
        version=settings.app_version,
        public_base_url=settings.public_base_url,
        api_prefix=settings.api_prefix,
        ai_provider=str(primary["name"]) if primary else "provider_pool",
        ai_model=str(primary["main_text_model"]) if primary else "",
        ai_enabled=bool(primary and primary.get("api_key_configured") and primary.get("main_text_model")),
    )
