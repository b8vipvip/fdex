from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.core.config import get_settings
from app.db.models import ProjectAsset, User
from app.db.session import get_db
from app.schemas.analysis import AnalysisResultRead
from app.schemas.asset import AssetRead
from app.services.analysis_service import AnalysisService
from app.services.file_classifier import classify_file

router = APIRouter(tags=["assets"])


@router.post("/projects/{project_id}/assets/upload", response_model=AssetRead)
def upload_asset(project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    settings = get_settings()
    upload_root = Path(settings.upload_dir) / str(project_id)
    upload_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "upload.bin").suffix
    safe_name = f"{uuid4().hex}{suffix}"
    target = upload_root / safe_name
    size = 0
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    with target.open("wb") as buffer:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                buffer.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_size_mb}MB 限制")
            buffer.write(chunk)
    file_type = classify_file(file.filename or safe_name, file.content_type)
    asset = ProjectAsset(
        project_id=project_id,
        filename=safe_name,
        original_filename=file.filename or safe_name,
        file_path=str(target),
        file_type=file_type,
        mime_type=file.content_type or "application/octet-stream",
        file_size=size,
        status="uploaded",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.get("/projects/{project_id}/assets", response_model=list[AssetRead])
def list_assets(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    return db.query(ProjectAsset).filter(ProjectAsset.project_id == project_id).order_by(ProjectAsset.created_at.desc()).all()


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(asset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    asset = db.get(ProjectAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="资料不存在")
    get_owned_project(db, asset.project_id, user)
    return asset


@router.post("/assets/{asset_id}/analyze", response_model=AnalysisResultRead)
def analyze_asset(asset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    asset = db.get(ProjectAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="资料不存在")
    get_owned_project(db, asset.project_id, user)
    try:
        return AnalysisService(db).analyze_asset(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
