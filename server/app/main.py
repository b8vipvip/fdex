from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# Install the GitHub App installation-authority project store before route/runtime modules import
# `agent_project_store` from the compatibility module.
from app.github_app_bootstrap import install_github_app_project_store

install_github_app_project_store()

from app.admin_routes import router as admin_router
from app.agent_admin_routes import router as agent_admin_router
from app.agent_policy_portal_routes import router as agent_policy_portal_router
from app.agent_routes import router as agent_router
from app.auth_routes import router as auth_router
from app.center_auth_middleware import CenterUserAuthMiddleware
from app.client_ai import router as client_ai_router
from app.client_log_admin_routes import router as client_log_admin_router
from app.client_runtime_log_routes import router as client_runtime_log_router
from app.client_update import router as client_update_router
from app.codex_input_center_routes import router as codex_input_center_router
from app.codex_provider_admin_routes import router as codex_provider_admin_router
from app.codex_provider_rollout import install_codex_provider_rollout_runtime
from app.codex_provider_smoke_mcp import router as codex_provider_smoke_mcp_router
from app.codex_task_input_routes import router as codex_task_input_router
from app.config import SERVER_DIR, get_settings
from app.fdex_memory import close_memory_coordinator
from app.github_app_admin_routes import router as github_app_admin_router
from app.github_app_flow_cleanup import start_github_app_flow_cleanup, stop_github_app_flow_cleanup
from app.github_app_portal_routes import router as github_app_portal_router
from app.mail_admin_routes import router as mail_admin_router
from app.memory_middleware_streamsafe import StreamSafeFdexMemoryMiddleware
from app.provider_admin import router as provider_admin_router
from app.provider_manager import provider_store
from app.provider_protocol_runtime import install_provider_protocol_runtime
from app.realtime_diagnostic_admin import router as realtime_diagnostic_admin_router
from app.realtime_voice import router as realtime_voice_router
from app.remote_mcp_gateway import remote_mcp_lease_store, router as remote_mcp_gateway_router
from app.request_trace import log_ai_event, request_id_for
from app.schemas import HealthResponse, PublicConfigResponse, VersionResponse
from app.update_monitor_routes import router as update_monitor_router
from app.user_account_auth_routes import router as user_account_auth_router
from app.user_admin_routes import router as user_admin_router
from app.user_agent_task_routes import router as user_agent_task_router
from app.user_codex_event_routes import router as user_codex_event_router

# The legacy provider manager already stored protocol_order, but the text runtime previously ignored
# it and always called /chat/completions. Install the protocol-aware runtime before Web app routes
# import and start invoking client_ai().
install_provider_protocol_runtime()

# Phase 7.33 separates generic Provider health from real Codex compatibility. Every production Codex
# launch/status seam is rebound to the fresh-full compatibility selector here. The explicit admin
# smoke runner still uses select_codex_provider_from([provider]) so an unverified Provider can be
# tested without creating a circular "must already be unlocked to run the unlock test" dependency.
install_codex_provider_rollout_runtime()

from app.user_app_routes import router as user_app_router
from app.agent_identity_runtime import install_agent_identity_runtime

# Generalize the old company/employee presentation into user-defined 智体 while retaining the old
# storage kinds and URLs for backward-compatible data migration.
install_agent_identity_runtime()

from app.employee_chat_runtime import install_employee_chat_runtime

# Coding Agent tooling wraps the generalized responder after its identity prompt is installed.
install_employee_chat_runtime()

from app.agent_identity_routes import router as agent_identity_router
from app.user_chat_api_routes import router as user_chat_api_router
from app.user_home_routes import router as user_home_router
from app.user_login_routes import router as user_login_router
from app.user_portal_routes import router as user_portal_router

settings = get_settings()
app = FastAPI(title=settings.app_name, version=settings.app_version)

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
app.add_middleware(StreamSafeFdexMemoryMiddleware)
app.add_middleware(CenterUserAuthMiddleware)


@app.middleware("http")
async def trace_client_ai_requests(request: Request, call_next):
    if request.url.path not in {"/api/client/ai", "/api/client/ai/stream"}:
        return await call_next(request)
    request_id = request_id_for(request)
    started = perf_counter()
    client_host = request.client.host if request.client else ""
    log_ai_event(
        "http_request_begin",
        request_id,
        method=request.method,
        path=request.url.path,
        content_length=request.headers.get("content-length", ""),
        content_type=request.headers.get("content-type", ""),
        mode=request.headers.get("x-fdex-request-mode", ""),
        client=client_host,
    )
    try:
        response = await call_next(request)
    except Exception as exc:
        log_ai_event(
            "http_request_exception",
            request_id,
            level="error",
            elapsed_ms=int((perf_counter() - started) * 1000),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        raise
    response.headers["X-FDEX-Request-ID"] = request_id
    log_ai_event(
        "http_request_end",
        request_id,
        status_code=response.status_code,
        elapsed_ms=int((perf_counter() - started) * 1000),
    )
    return response


static_dir = Path(SERVER_DIR) / "app" / "static"
release_dir = Path(settings.release_cache_dir)
generated_dir = Path(settings.app_dir) / "server" / "data" / "generated"
release_dir.mkdir(parents=True, exist_ok=True)
generated_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/downloads", StaticFiles(directory=release_dir), name="downloads")
app.mount("/generated", StaticFiles(directory=generated_dir), name="generated")
# Internal capability routes are intentionally not account/API surfaces. Their handlers additionally
# require an actual loopback TCP peer and an unguessable task/smoke capability before touching state.
# Mount them before broad user routers so no compatibility route can shadow them.
app.include_router(remote_mcp_gateway_router)
app.include_router(codex_provider_smoke_mcp_router)
app.include_router(user_login_router)
app.include_router(user_account_auth_router)
app.include_router(user_home_router)
# The generalized create/update handlers intentionally precede the compatibility router so legacy
# URLs keep working without exposing the retired company/industry/department/position contract.
app.include_router(agent_identity_router)
app.include_router(user_app_router)
app.include_router(user_chat_api_router)
app.include_router(user_agent_task_router)
app.include_router(codex_input_center_router)
app.include_router(codex_task_input_router)
app.include_router(user_codex_event_router)
app.include_router(agent_policy_portal_router)
app.include_router(user_portal_router)
app.include_router(github_app_portal_router)
app.include_router(admin_router)
app.include_router(client_log_admin_router)
app.include_router(github_app_admin_router)
app.include_router(mail_admin_router)
app.include_router(user_admin_router)
app.include_router(agent_admin_router)
app.include_router(codex_provider_admin_router)
app.include_router(update_monitor_router)
app.include_router(provider_admin_router)
app.include_router(realtime_diagnostic_admin_router)
app.include_router(auth_router)
app.include_router(client_runtime_log_router)
app.include_router(client_ai_router)
app.include_router(realtime_voice_router)
app.include_router(client_update_router)
app.include_router(agent_router)


@app.on_event("startup")
async def start_security_cleanup_tasks() -> None:
    await start_github_app_flow_cleanup()
    # Leases are short-lived and raw tokens are never stored, but reconcile expired rows on each
    # process start so a worker crash cannot leave durable state that still looks active.
    remote_mcp_lease_store().purge_expired()


@app.on_event("shutdown")
async def shutdown_memory_clients() -> None:
    await stop_github_app_flow_cleanup()
    await close_memory_coordinator()


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
        "account": f"{settings.public_base_url}/account",
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