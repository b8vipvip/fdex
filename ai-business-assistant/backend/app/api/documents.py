from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from app.api.deps import get_current_user
from app.api.projects import get_owned_project
from app.db.models import ProjectReport, User
from app.db.session import get_db
from app.schemas.document import ReportRead
from app.utils.markdown_export import as_markdown_download

router = APIRouter(prefix="/reports", tags=["documents"])


@router.get("/{report_id}", response_model=ReportRead)
def get_report(report_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = db.get(ProjectReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    get_owned_project(db, report.project_id, user)
    return report


@router.get("/{report_id}/export-md")
def export_report(report_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = db.get(ProjectReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    get_owned_project(db, report.project_id, user)
    filename = f"report-{report.id}-{report.report_type}.md"
    return Response(
        content=as_markdown_download(report.content_markdown),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
