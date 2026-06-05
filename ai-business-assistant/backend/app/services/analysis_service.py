import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from app.db.models import AITaskLog, AssetAnalysisResult, Project, ProjectAsset, ProjectMessage
from app.services.ai_providers import MockAIProvider
from app.services.ai_router import route_analyzer
from app.services.document_service import upsert_report


class AnalysisService:
    def __init__(self, db: Session):
        self.db = db
        self.provider = MockAIProvider()

    def _log(self, project_id: int, asset_id: int | None, task_type: str, status: str, input_summary: str = "") -> AITaskLog:
        log = AITaskLog(project_id=project_id, asset_id=asset_id, task_type=task_type, status=status, input_summary=input_summary)
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def _finish_log(self, log: AITaskLog, status: str, output: str = "", error: str = "") -> None:
        log.status = status
        log.output_summary = output[:500]
        log.error_message = error
        log.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    def analyze_asset(self, asset_id: int) -> AssetAnalysisResult:
        asset = self.db.get(ProjectAsset, asset_id)
        if not asset:
            raise ValueError("资料不存在")
        project = self.db.get(Project, asset.project_id)
        if not project:
            raise ValueError("项目不存在")
        analyzer = route_analyzer(asset.file_type)
        log = self._log(project.id, asset.id, f"analyze_{analyzer}", "running", asset.original_filename)
        asset.status = "analyzing"
        self.db.commit()
        try:
            level = project.user.professional_level if project.user else "business"
            text_preview = self._read_text_preview(asset)
            method_map = {
                "text_ai": lambda: self.provider.analyze_text(text_preview or project.description, level),
                "vision_ai": lambda: self.provider.analyze_image(asset.file_path, level),
                "audio_ai": lambda: self.provider.analyze_audio(asset.file_path, level),
                "video_ai": lambda: self.provider.analyze_video(asset.file_path, level),
                "table_ai": lambda: self.provider.analyze_table(asset.file_path, level),
                "document_ai": lambda: self.provider.analyze_document(asset.file_path, level),
                "code_ai": lambda: self.provider.analyze_code(asset.file_path, level),
            }
            markdown, data = method_map[analyzer]()
            result = AssetAnalysisResult(
                asset_id=asset.id,
                project_id=project.id,
                analyzer_type=analyzer,
                summary=markdown,
                structured_json=json.dumps(data, ensure_ascii=False),
            )
            self.db.add(result)
            asset.status = "analyzed"
            self._finish_log(log, "success", markdown)
            self.db.commit()
            self.db.refresh(result)
            return result
        except Exception as exc:
            asset.status = "failed"
            self._finish_log(log, "failed", error=str(exc))
            self.db.commit()
            raise

    def _read_text_preview(self, asset: ProjectAsset) -> str:
        if asset.file_type not in {"text", "code", "spreadsheet"}:
            return ""
        try:
            return Path(asset.file_path).read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            return ""

    def _context(self, project: Project) -> dict:
        messages = self.db.query(ProjectMessage).filter(ProjectMessage.project_id == project.id).all()
        results = self.db.query(AssetAnalysisResult).filter(AssetAnalysisResult.project_id == project.id).all()
        return {
            "title": project.title,
            "description": project.description,
            "project_type": project.project_type,
            "messages": [m.content for m in messages],
            "asset_results": [r.summary for r in results],
        }

    def analyze_project(self, project_id: int) -> list:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("项目不存在")
        log = self._log(project.id, None, "comprehensive_analysis", "running", project.description)
        markdown, data = self.provider.comprehensive_analysis(self._context(project))
        project.project_type = data.get("project_type", "unknown")
        project.requirement_score = float(data.get("requirement_score", 60))
        project.status = "analyzed"
        comprehensive = upsert_report(self.db, project.id, "comprehensive_analysis", markdown, data)
        reports = [comprehensive]
        reports.extend(self.generate_final_outputs(project.id))
        self._finish_log(log, "success", markdown)
        self.db.commit()
        return reports

    def classify_project_type(self, project_id: int) -> str:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("项目不存在")
        if project.project_type == "unknown":
            self.analyze_project(project_id)
        return project.project_type

    def generate_final_outputs(self, project_id: int) -> list:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("项目不存在")
        context = self._context(project)
        context["project_type"] = project.project_type
        reports = []
        requirement_md = f"""# 需求分析报告

## 1. 项目背景
{project.description or '用户尚未填写详细背景，建议继续补充业务目标、使用对象和当前流程。'}

## 2. 核心目标
- 把用户输入和上传资料整理为可执行方案
- 明确项目类型、MVP 范围和交付路径
- 输出方便业务、产品、技术共同评审的文档

## 3. 功能模块清单
- 需求输入与补充
- 文件上传与类型识别
- 单资料 AI 分析
- 综合分析与项目类型判断
- Markdown 报告查看、复制和导出

## 4. 待确认问题
- 首批使用者是谁
- 哪些资料类型最常见
- 是否需要多人协作和权限
- 是否需要接入真实 AI 模型

## 5. 验收标准
- 用户可以完成从输入需求到导出报告的完整闭环
- 报告内容结构清晰，可直接交给程序员、AI 编程工具或业务执行人员使用
"""
        reports.append(upsert_report(self.db, project.id, "requirement_analysis", requirement_md, {"report_type": "requirement_analysis"}))
        if project.project_type in {"software_development", "office_automation"}:
            for report_type, generator in [
                ("prd", self.provider.generate_prd),
                ("technical_solution", self.provider.generate_technical_solution),
                ("developer_prompt", self.provider.generate_developer_prompt),
            ]:
                md, data = generator(context)
                reports.append(upsert_report(self.db, project.id, report_type, md, data))
        else:
            md, data = self.provider.generate_industry_solution(context)
            reports.append(upsert_report(self.db, project.id, "industry_solution", md, data))
            reports.append(upsert_report(self.db, project.id, "sop", md.replace("# 行业解决方案", "# 执行 SOP"), data))
            risk = "# 风险分析报告\n\n## 1. 范围风险\n需求不清会导致返工。\n\n## 2. 执行风险\n人员分工不明确会影响推进。\n\n## 3. 成本风险\n建议先做小范围试点再扩大投入。\n"
            reports.append(upsert_report(self.db, project.id, "risk_report", risk, {"report_type": "risk_report"}))
        return reports
