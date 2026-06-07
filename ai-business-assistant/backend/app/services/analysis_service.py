import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from app.db.models import AITaskLog, AssetAnalysisResult, Project, ProjectAsset, ProjectMessage
from app.services.ai_providers import MockAIProvider, create_ai_provider
from app.services.privacy_service import desensitize_text, detect_sensitive_text
from app.services.ai_router import route_analyzer
from app.services.document_service import upsert_report
from app.services.project_context_service import build_project_context


class AnalysisService:
    def __init__(self, db: Session):
        self.db = db
        self.provider = create_ai_provider()

    def _safe_log_text(self, text: str, limit: int = 500) -> str:
        if not text:
            return ""
        detected = detect_sensitive_text(text)
        safe_text = desensitize_text(text)["desensitized_text"] if detected["is_sensitive"] else text
        return safe_text[:limit]

    def _log(self, project_id: int, asset_id: int | None, task_type: str, status: str, input_summary: str = "") -> AITaskLog:
        log = AITaskLog(project_id=project_id, asset_id=asset_id, task_type=task_type, status=status, input_summary=self._safe_log_text(input_summary))
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def _finish_log(self, log: AITaskLog, status: str, output: str = "", error: str = "") -> None:
        log.status = status
        log.output_summary = self._safe_log_text(output)
        log.error_message = self._safe_log_text(error)
        log.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    def analyze_asset(self, asset_id: int) -> AssetAnalysisResult:
        asset = self.db.get(ProjectAsset, asset_id)
        if not asset:
            raise ValueError("资料不存在")
        project = self.db.get(Project, asset.project_id)
        if not project:
            raise ValueError("项目不存在")
        if asset.status == "local_only" or not asset.file_path and not asset.desensitized_path:
            raise ValueError("本地模式资料未上传云端，无法进行云端 AI 分析；请选择临时上传或脱敏上传。")
        if asset.status == "need_user_decision":
            raise ValueError("该资料可能包含敏感信息，请先完成隐私处理选择。")
        analyzer = route_analyzer(asset.file_type)
        log = self._log(project.id, asset.id, f"analyze_{analyzer}", "running", f"asset_id={asset.id}; file_type={asset.file_type}; privacy_level={asset.privacy_level}")
        asset.status = "analyzing"
        self.db.commit()
        try:
            level = project.user.professional_level if project.user else "business"
            text_preview = self._read_text_preview(asset)
            provider = self._provider_for_project(project)
            analysis_path = asset.desensitized_path or asset.file_path
            method_map = {
                "text_ai": lambda: provider.analyze_text(text_preview or project.description, level),
                "vision_ai": lambda: provider.analyze_image(analysis_path, level),
                "audio_ai": lambda: provider.analyze_audio(analysis_path, level),
                "video_ai": lambda: provider.analyze_video(analysis_path, level),
                "table_ai": lambda: provider.analyze_table(analysis_path, level),
                "document_ai": lambda: provider.analyze_document(analysis_path, level),
                "code_ai": lambda: provider.analyze_code(analysis_path, level),
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
            if project.storage_mode == "temporary" or project.data_retention_policy == "delete_after_analysis":
                self._delete_original(asset)
            self._finish_log(log, "success", markdown)
            self.db.commit()
            self.db.refresh(result)
            return result
        except Exception as exc:
            asset.status = "failed"
            self._finish_log(log, "failed", error=str(exc))
            self.db.commit()
            raise

    def _delete_original(self, asset: ProjectAsset) -> None:
        if asset.file_path:
            Path(asset.file_path).unlink(missing_ok=True)
        asset.original_deleted_at = datetime.now(timezone.utc)
        asset.file_path = ""

    def _provider_for_project(self, project: Project):
        if not project.allow_third_party_ai:
            return MockAIProvider()
        return self.provider

    def _read_text_preview(self, asset: ProjectAsset) -> str:
        if asset.file_type not in {"text", "code", "spreadsheet"}:
            return ""
        source = asset.desensitized_path or asset.file_path
        if not source:
            return ""
        try:
            text = Path(source).read_text(encoding="utf-8", errors="ignore")[:4000]
            if asset.is_sensitive and not asset.desensitized_path:
                return desensitize_text(text)["desensitized_text"]
            return text
        except OSError:
            return ""

    def _context(self, project: Project) -> dict:
        project_context = build_project_context(self.db, project.id)
        messages = project_context["project_messages"]
        results = project_context["asset_analysis_results"]
        safe = project.auto_desensitize
        clean = (lambda value: desensitize_text(value)["desensitized_text"] if safe and detect_sensitive_text(value)["is_sensitive"] else value)
        return {
            "title": clean(project.title),
            "description": clean(project.description),
            "project_type": project.project_type,
            "storage_mode": project.storage_mode,
            "allow_third_party_ai": project.allow_third_party_ai,
            "auto_desensitize": project.auto_desensitize,
            "messages": [clean(m.content) for m in messages],
            "asset_results": [clean(r.summary) for r in results],
            "related_employee_messages": [clean(m.content) for m in project_context["related_employee_messages"]],
            "context_markdown": clean(project_context["context_markdown"]),
        }

    def analyze_project(self, project_id: int) -> list:
        project = self.db.get(Project, project_id)
        if not project:
            raise ValueError("项目不存在")
        log = self._log(project.id, None, "comprehensive_analysis", "running", project.description)
        try:
            provider = self._provider_for_project(project)
            markdown, data = provider.comprehensive_analysis(self._context(project))
            project.project_type = data.get("project_type", "unknown")
            project.requirement_score = float(data.get("requirement_score", 60))
            project.status = "analyzed"
            comprehensive = upsert_report(self.db, project.id, "comprehensive_analysis", markdown, data)
            reports = [comprehensive]
            reports.extend(self.generate_final_outputs(project.id))
            self._finish_log(log, "success", markdown)
            self.db.commit()
            return reports
        except Exception as exc:
            project.status = "failed"
            self._finish_log(log, "failed", error=str(exc))
            self.db.commit()
            raise

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
                ("prd", self._provider_for_project(project).generate_prd),
                ("technical_solution", self._provider_for_project(project).generate_technical_solution),
                ("developer_prompt", self._provider_for_project(project).generate_developer_prompt),
            ]:
                log = self._log(project.id, None, report_type, "running", project.title)
                try:
                    md, data = generator(context)
                    reports.append(upsert_report(self.db, project.id, report_type, md, data))
                    self._finish_log(log, "success", md)
                except Exception as exc:
                    self._finish_log(log, "failed", error=str(exc))
                    raise
        else:
            log = self._log(project.id, None, "industry_solution", "running", project.title)
            try:
                md, data = self._provider_for_project(project).generate_industry_solution(context)
                reports.append(upsert_report(self.db, project.id, "industry_solution", md, data))
                self._finish_log(log, "success", md)
            except Exception as exc:
                self._finish_log(log, "failed", error=str(exc))
                raise
            reports.append(upsert_report(self.db, project.id, "sop", md.replace("# 行业解决方案", "# 执行 SOP"), data))
            risk = "# 风险分析报告\n\n## 1. 范围风险\n需求不清会导致返工。\n\n## 2. 执行风险\n人员分工不明确会影响推进。\n\n## 3. 成本风险\n建议先做小范围试点再扩大投入。\n"
            reports.append(upsert_report(self.db, project.id, "risk_report", risk, {"report_type": "risk_report"}))
        return reports
