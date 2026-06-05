import json
from sqlalchemy.orm import Session
from app.db.models import ProjectReport

TITLE_MAP = {
    "requirement_analysis": "需求分析报告",
    "comprehensive_analysis": "项目综合分析报告",
    "prd": "PRD 产品需求文档",
    "technical_solution": "技术方案",
    "industry_solution": "行业解决方案",
    "developer_prompt": "AI 编程工具开发提示词",
    "sop": "执行 SOP",
    "risk_report": "风险分析报告",
}


def upsert_report(db: Session, project_id: int, report_type: str, content: str, data: dict | None = None) -> ProjectReport:
    report = db.query(ProjectReport).filter(ProjectReport.project_id == project_id, ProjectReport.report_type == report_type).first()
    if report is None:
        report = ProjectReport(project_id=project_id, report_type=report_type, title=TITLE_MAP.get(report_type, report_type), content_markdown=content)
        db.add(report)
    else:
        report.content_markdown = content
    report.structured_json = json.dumps(data or {}, ensure_ascii=False)
    db.commit()
    db.refresh(report)
    return report
