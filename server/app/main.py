from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import HealthResponse, VersionResponse

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


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        time=datetime.now(timezone.utc),
    )


@app.get("/api/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )
