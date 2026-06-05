from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.db.models import ProjectReport, User
from app.db.session import get_db
from app.schemas.document import ReportRead
from app.services.analysis_service import AnalysisService

router = APIRouter(prefix="/projects", tags=["analysis"])


@router.post("/{project_id}/analyze", response_model=list[ReportRead])
def analyze_project(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    try:
        return AnalysisService(db).analyze_project(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}/reports", response_model=list[ReportRead])
def list_reports(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_project(db, project_id, user)
    return db.query(ProjectReport).filter(ProjectReport.project_id == project_id).order_by(ProjectReport.updated_at.desc()).all()
