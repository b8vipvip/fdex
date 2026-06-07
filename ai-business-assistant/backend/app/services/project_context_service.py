from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.db.models import AssetAnalysisResult, EmployeeMessage, Project, ProjectMessage


def build_project_context(db: Session, project_id: int) -> dict:
    project = db.get(Project, project_id)
    if not project: raise ValueError("项目不存在")
    project_messages = db.query(ProjectMessage).filter(ProjectMessage.project_id == project_id).order_by(ProjectMessage.created_at).all()
    asset_results = db.query(AssetAnalysisResult).filter(AssetAnalysisResult.project_id == project_id).order_by(AssetAnalysisResult.created_at).all()
    recent = datetime.now(timezone.utc) - timedelta(days=30)
    employee_messages = db.query(EmployeeMessage).filter(EmployeeMessage.user_id == project.user_id).filter(or_(EmployeeMessage.project_id == project_id, EmployeeMessage.content.like(f"%{project.title}%"), EmployeeMessage.created_at >= recent)).order_by(EmployeeMessage.created_at.desc()).limit(80).all()
    sections = [f"# 项目：{project.title}", project.description or "暂无项目描述"]
    if project_messages: sections.append("## 项目持续补充\n" + "\n".join(f"- {m.content}" for m in project_messages))
    if asset_results: sections.append("## 资料分析结果\n" + "\n".join(f"- {r.summary}" for r in asset_results))
    if employee_messages: sections.append("## 相关员工聊天\n" + "\n".join(f"- [{m.role}] {m.content}" for m in reversed(employee_messages)))
    return {"project": project, "project_messages": project_messages, "asset_analysis_results": asset_results, "related_employee_messages": employee_messages, "context_markdown": "\n\n".join(sections)}
