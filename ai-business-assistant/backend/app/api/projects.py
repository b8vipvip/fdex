from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.db.models import Project, ProjectMessage, User
from app.db.session import get_db
from app.schemas.project import MessageCreate, MessageRead, ProjectCreate, ProjectRead, ProjectUpdate

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
    project = Project(user_id=user.id, title=payload.title, description=payload.description, requirement_score=25 if payload.description else 0)
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
