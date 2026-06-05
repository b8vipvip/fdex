from datetime import datetime, timedelta, timezone
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
from app.schemas.asset import AssetRead, LocalOnlyAssetCreate
from app.schemas.privacy import PrivacyDecisionRequest
from app.services.ai_providers import AIProviderError
from app.services.analysis_service import AnalysisService
from app.services.file_classifier import classify_file
from app.services.privacy_service import desensitize_text, detect_sensitive_text

router = APIRouter(tags=["assets"])
TEXT_FILE_TYPES = {"text", "code", "spreadsheet"}
RETENTION_DAYS = {"delete_after_1_day": 1, "delete_after_7_days": 7, "delete_after_30_days": 30}


def _retention_deadline(policy: str) -> datetime | None:
    if policy in {"delete_after_analysis", "keep_forever"}:
        return None
    days = RETENTION_DAYS.get(policy)
    return datetime.now(timezone.utc) + timedelta(days=days) if days else None


def _read_preview(path: Path, file_type: str, limit: int = 8000) -> str:
    if file_type not in TEXT_FILE_TYPES:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _attach_detection(asset: ProjectAsset, detection: dict | None) -> ProjectAsset:
    if detection:
        setattr(asset, "privacy_detection", detection)
    return asset


def _delete_original(asset: ProjectAsset) -> None:
    if asset.file_path:
        Path(asset.file_path).unlink(missing_ok=True)
    asset.original_deleted_at = datetime.now(timezone.utc)
    asset.file_path = ""


def _write_desensitized_copy(asset: ProjectAsset) -> None:
    if not asset.file_path:
        return
    source = Path(asset.file_path)
    try:
        text = source.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    result = desensitize_text(text)
    target = source.with_name(f"{source.stem}.desensitized{source.suffix or '.txt'}")
    target.write_text(result["desensitized_text"], encoding="utf-8")
    asset.desensitized_path = str(target)


@router.post("/projects/{project_id}/assets/upload", response_model=AssetRead)
def upload_asset(project_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, user)
    settings = get_settings()
    original_name = file.filename or "upload.bin"
    file_type = classify_file(original_name, file.content_type)

    # local_only MVP: do not persist raw file on the backend; frontend should keep the file locally.
    if project.storage_mode == "local_only":
        while file.file.read(1024 * 1024):
            pass
        filename_detection = detect_sensitive_text(original_name)
        asset = ProjectAsset(
            project_id=project_id,
            filename="",
            original_filename=original_name,
            file_path="",
            file_type=file_type,
            mime_type=file.content_type or "application/octet-stream",
            file_size=0,
            status="local_only",
            privacy_level=filename_detection["privacy_level"],
            is_sensitive=filename_detection["is_sensitive"],
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return _attach_detection(asset, filename_detection)

    upload_root = Path(settings.upload_dir) / str(project_id)
    upload_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix
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

    content_preview = _read_preview(target, file_type)
    filename_detection = detect_sensitive_text(original_name)
    content_detection = detect_sensitive_text(content_preview)
    detected_items = filename_detection["detected_items"] + content_detection["detected_items"]
    privacy_level = "highly_sensitive" if "highly_sensitive" in {filename_detection["privacy_level"], content_detection["privacy_level"]} else (
        "sensitive" if filename_detection["is_sensitive"] or content_detection["is_sensitive"] else "normal"
    )
    is_sensitive = privacy_level in {"sensitive", "highly_sensitive"}
    detection = {
        "is_sensitive": is_sensitive,
        "privacy_level": privacy_level,
        "detected_items": detected_items,
        "suggested_action": "desensitize_before_upload" if is_sensitive else "upload_allowed",
    }

    status = "uploaded"
    retention_deadline = _retention_deadline(project.data_retention_policy)
    if project.storage_mode == "hybrid" and is_sensitive:
        status = "need_user_decision"
    elif project.storage_mode == "temporary":
        retention_deadline = None
    elif project.storage_mode == "cloud" and privacy_level == "highly_sensitive":
        status = "uploaded_sensitive_warning"

    asset = ProjectAsset(
        project_id=project_id,
        filename=safe_name,
        original_filename=original_name,
        file_path=str(target),
        file_type=file_type,
        mime_type=file.content_type or "application/octet-stream",
        file_size=size,
        status=status,
        privacy_level=privacy_level,
        is_sensitive=is_sensitive,
        retention_deadline=retention_deadline,
    )
    db.add(asset)
    db.flush()
    if is_sensitive and project.auto_desensitize and file_type in TEXT_FILE_TYPES:
        _write_desensitized_copy(asset)
        if project.storage_mode == "cloud" and status == "uploaded_sensitive_warning":
            status = "uploaded"
            asset.status = status
    db.commit()
    db.refresh(asset)
    return _attach_detection(asset, detection)


@router.post("/projects/{project_id}/assets/local-only", response_model=AssetRead)
def create_local_only_asset(project_id: int, payload: LocalOnlyAssetCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, user)
    if project.storage_mode != "local_only":
        raise HTTPException(status_code=400, detail="仅本地模式项目才能使用本地资料占位接口")
    file_type = classify_file(payload.original_filename, payload.mime_type)
    filename_detection = detect_sensitive_text(payload.original_filename)
    asset = ProjectAsset(
        project_id=project_id,
        filename="",
        original_filename=payload.original_filename,
        file_path="",
        file_type=file_type,
        mime_type=payload.mime_type or "application/octet-stream",
        file_size=payload.file_size,
        status="local_only",
        privacy_level=filename_detection["privacy_level"],
        is_sensitive=filename_detection["is_sensitive"],
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _attach_detection(asset, filename_detection)


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


@router.post("/assets/{asset_id}/privacy-decision", response_model=AssetRead)
def decide_asset_privacy(asset_id: int, payload: PrivacyDecisionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    asset = db.get(ProjectAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="资料不存在")
    get_owned_project(db, asset.project_id, user)
    if payload.decision == "desensitize":
        _write_desensitized_copy(asset)
        if not asset.desensitized_path:
            raise HTTPException(status_code=400, detail="当前文件暂不支持自动脱敏，请选择临时分析、仅本地保存或确认原文上传")
        asset.status = "uploaded"
    elif payload.decision == "temporary":
        asset.status = "uploaded"
        asset.retention_deadline = None
    elif payload.decision == "local_only":
        _delete_original(asset)
        asset.status = "local_only"
    elif payload.decision == "confirm_upload":
        asset.status = "uploaded"
    db.commit()
    db.refresh(asset)
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
        raise HTTPException(status_code=400 if "隐私" in str(exc) or "本地" in str(exc) else 404, detail=str(exc)) from exc
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
