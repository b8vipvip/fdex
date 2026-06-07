from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.models import Project, ProjectAsset, ProjectMessage, User
from app.db.session import get_db
from app.schemas.privacy import PrivacySummaryRead
from app.schemas.project import MessageCreate, MessageRead, ProjectCreate, ProjectRead, ProjectUpdate
from app.services.project_context_service import build_project_context

router = APIRouter(prefix="/projects", tags=["projects"])


def get_owned_project(db: Session, project_id: int, user: User) -> Project:
    project = db.get(Project, project_id)
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Project).filter(Project.user_id == user.id).order_by(Project.updated_at.desc()).all()


@router.post("", response_model=ProjectRead)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.professional_level:
        user.professional_level = payload.professional_level
    project = Project(
        user_id=user.id,
        title=payload.title,
        description=payload.description,
        requirement_score=25 if payload.description else 0,
        storage_mode=payload.storage_mode,
        data_retention_policy=payload.data_retention_policy,
        allow_third_party_ai=payload.allow_third_party_ai,
        auto_desensitize=payload.auto_desensitize,
    )
    db.add(project)
    db.flush()
    if payload.description:
        db.add(ProjectMessage(project_id=project.id, role="user", content=payload.description))
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_owned_project(db, project_id, user)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, user)
    db.delete(project)
    db.commit()
    return {"ok": True}




@router.get("/{project_id}/privacy-summary", response_model=PrivacySummaryRead)
def privacy_summary(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, user)
    assets = db.query(ProjectAsset).filter(ProjectAsset.project_id == project_id).all()
    deadlines = [asset.retention_deadline for asset in assets if asset.retention_deadline]
    return {
        "project_id": project.id,
        "storage_mode": project.storage_mode,
        "data_retention_policy": project.data_retention_policy,
        "allow_third_party_ai": project.allow_third_party_ai,
        "auto_desensitize": project.auto_desensitize,
        "total_assets": len(assets),
        "sensitive_assets": sum(1 for asset in assets if asset.is_sensitive),
        "highly_sensitive_assets": sum(1 for asset in assets if asset.privacy_level == "highly_sensitive"),
        "pending_decision_assets": sum(1 for asset in assets if asset.status == "need_user_decision"),
        "retention_deadline": min(deadlines) if deadlines else None,
    }

@router.post("/{project_id}/messages", response_model=MessageRead)
def create_message(project_id: int, payload: MessageCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    message = ProjectMessage(project_id=project_id, role=payload.role, content=payload.content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("/{project_id}/messages", response_model=list[MessageRead])
def list_messages(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    return db.query(ProjectMessage).filter(ProjectMessage.project_id == project_id).order_by(ProjectMessage.created_at.asc()).all()


@router.get("/{project_id}/context")
def get_project_context(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    context = build_project_context(db, project_id)
    return {"project": {"id": context["project"].id, "title": context["project"].title, "description": context["project"].description}, "project_messages": [{"id": x.id, "content": x.content, "role": x.role} for x in context["project_messages"]], "asset_analysis_results": [{"id": x.id, "summary": x.summary} for x in context["asset_analysis_results"]], "related_employee_messages": [{"id": x.id, "employee_id": x.employee_id, "role": x.role, "content": x.content} for x in context["related_employee_messages"]], "context_markdown": context["context_markdown"]}
