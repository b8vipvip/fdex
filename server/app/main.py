from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import HealthResponse, PublicConfigResponse, VersionResponse

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origin_list != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": f"{settings.public_base_url}/docs",
        "health": f"{settings.public_base_url}{settings.api_prefix}/health",
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
    return PublicConfigResponse(
        service=settings.app_name,
        version=settings.app_version,
        public_base_url=settings.public_base_url,
        api_prefix=settings.api_prefix,
        ai_provider=settings.ai_provider,
        ai_model=settings.ai_model,
        ai_enabled=settings.ai_enabled,
    )
