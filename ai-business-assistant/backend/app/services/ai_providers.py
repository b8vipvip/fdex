import base64
import json
import mimetypes
import re
import textwrap
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings


_PROJECT_TYPES = {"software_development", "office_automation", "business_operation", "data_analysis", "consulting", "unknown"}


def _json(project_type: str = "business_operation", score: float = 72) -> dict:
    return {"project_type": project_type, "requirement_score": score, "modules": ["需求收集", "资料分析", "方案生成", "文档导出"]}


class AIProviderError(RuntimeError):
    """Sanitized AI provider exception safe to show to users/logs."""


class BaseAIProvider(ABC):
    @abstractmethod
    def analyze_text(self, text: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_image(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_audio(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_video(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_table(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_document(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def analyze_code(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]: ...
    @abstractmethod
    def comprehensive_analysis(self, context: dict) -> tuple[str, dict]: ...
    @abstractmethod
    def generate_prd(self, context: dict) -> tuple[str, dict]: ...
    @abstractmethod
    def generate_technical_solution(self, context: dict) -> tuple[str, dict]: ...
    @abstractmethod
    def generate_industry_solution(self, context: dict) -> tuple[str, dict]: ...
    @abstractmethod
    def generate_developer_prompt(self, context: dict) -> tuple[str, dict]: ...
class MockAIProvider(BaseAIProvider):
    def _style_note(self, level: str) -> str:
        notes = {
            "beginner": "本报告会尽量使用大白话解释，必要技术词会补充说明。",
            "business": "本报告重点关注业务流程、运营效果和可落地动作。",
            "product": "本报告会补充 PRD、用户故事和验收标准视角。",
            "developer": "本报告会突出接口、数据结构和工程实现细节。",
            "auto": "本报告暂按业务视角输出，后续可由 AI 自动判断表达风格。",
        }
        return notes.get(level, notes["business"])

    def analyze_text(self, text: str, professional_level: str = "business") -> tuple[str, dict]:
        md = f"""# 文本资料分析报告

> {self._style_note(professional_level)}

## 1. 用户目标
用户希望把零散需求、业务资料和沟通内容整理为可以执行的项目方案。

## 2. 业务场景
资料可能来自日常运营、客户反馈、老板想法或项目会议记录。当前问题是信息分散，难以转化成明确任务。

## 3. 已识别需求
- 汇总用户输入和上传资料
- 判断项目属于开发类还是行业执行类
- 输出结构化报告、任务拆解和可复制提示词

## 4. 潜在功能模块
- 需求输入与追问
- 文件上传与类型识别
- AI 路由分析
- 综合报告生成
- Markdown 导出

## 5. 待确认问题
- 目标用户规模和使用频率
- 是否需要团队协作与权限控制
- 是否需要接入真实第三方 AI 模型

## 6. 风险提示
如果早期范围过大，会导致成本和周期失控，建议先完成核心闭环。
"""
        return md, _json("software_development" if any(k in text.lower() for k in ["app", "系统", "网站", "api", "软件", "小程序"]) else "business_operation")

    def analyze_image(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 图片资料分析报告

## 1. 图片内容推测
该图片可能包含页面截图、业务流程图、手写草图或运营素材。

## 2. 可能包含的业务信息
- 页面布局或功能入口
- 用户操作路径
- 品牌、产品或流程线索

## 3. 可能对应的系统页面
- 首页/工作台
- 列表页
- 详情页
- 表单页

## 4. 可转化为需求的功能点
- 将截图中的模块转化为页面组件
- 提取操作步骤并形成流程说明
- 标记需要二次确认的字段和按钮
""", _json()

    def analyze_audio(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 音频资料分析报告

## 1. 音频内容推测
音频可能是会议、访谈或需求口述记录。

## 2. 关键业务信息
建议转写后提取角色、目标、痛点、约束和验收标准。

## 3. 可执行动作
- 整理会议纪要
- 生成待确认问题
- 提炼任务清单
""", _json()

    def analyze_video(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 视频资料分析报告

## 1. 视频场景概述
视频可能记录了人工操作流程、软件演示或业务现场。

## 2. 推测操作流程
从开始动作、关键节点、异常处理和结束状态拆解流程。

## 3. 可自动化步骤
- 重复录入
- 表格统计
- 标准审批
- 通知提醒

## 4. 需要人工确认的节点
- 判断规则是否稳定
- 异常情况如何处理
- 是否涉及隐私数据
""", _json("office_automation")

    def analyze_table(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 表格资料分析报告

## 1. 表格用途推测
表格可能承载客户、订单、库存、排班、财务或运营数据。

## 2. 可识别字段
建议关注编号、名称、状态、负责人、时间、金额和备注等字段。

## 3. 可转化能力
- 数据导入
- 统计看板
- 自动分类
- 异常提醒
""", _json("data_analysis")

    def analyze_document(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 文档资料分析报告

## 1. 文档内容推测
文档可能是合同、制度、方案、PRD 或会议材料。

## 2. 关键信息
- 业务目标
- 约束条件
- 交付物
- 时间和责任人

## 3. 可转化为需求的功能点
- 文档摘要
- 条款提取
- 任务拆解
- 风险提示
""", _json("consulting")

    def analyze_code(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        return """# 代码资料分析报告

## 1. 代码用途推测
该文件可能包含业务逻辑、页面组件、接口定义或配置。

## 2. 技术线索
- 需要识别语言、框架和依赖
- 需要梳理模块边界与数据流

## 3. 可落地建议
- 生成重构建议
- 补充接口文档
- 形成开发任务清单
""", _json("software_development")

    def comprehensive_analysis(self, context: dict) -> tuple[str, dict]:
        text = f"{context.get('description', '')} {' '.join(context.get('messages', []))}".lower()
        project_type = "software_development" if any(k in text for k in ["系统", "软件", "app", "小程序", "网站", "开发", "api"]) else "business_operation"
        score = min(95, 45 + len(text) // 8 + len(context.get("asset_results", [])) * 8)
        md = f"""# 项目综合分析报告

## 1. 用户真实目标
用户希望把模糊想法和已有资料转化为一套可执行、可沟通、可验收的落地方案。

## 2. 当前业务痛点
- 业务语言和技术语言之间存在理解偏差
- 资料分散，缺少统一整理和判断
- 不清楚应该先做 MVP 还是一步到位

## 3. 资料来源总结
本次综合了项目描述、补充消息和 {len(context.get('asset_results', []))} 份资料分析结果。

## 4. 项目类型判断
系统判断项目类型为：**{project_type}**。判断依据是用户目标、关键词、资料类型和潜在交付物。

## 5. 推荐落地路线
1. 先明确目标用户、核心场景和验收标准
2. 用 1-2 周完成 MVP 方案和原型
3. 用小范围用户验证价值
4. 再决定是否扩展高级能力

## 6. MVP范围
- 用户输入需求
- 上传资料并识别类型
- 生成单资料分析报告
- 生成综合分析报告
- 输出开发或行业落地文档

## 7. 后续扩展方向
- 接入真实多模态 AI
- 团队协作和权限管理
- 知识库和行业模板
- 对象存储和异步任务队列

## 8. 待确认问题
- 预算和期望上线时间
- 首批用户是谁
- 是否需要私有化部署

## 9. 风险提醒
最大风险是范围膨胀。建议坚持 MVP 优先，只做最能验证价值的功能。
"""
        return md, _json(project_type, float(score))

    def generate_prd(self, context: dict) -> tuple[str, dict]:
        return f"""# PRD 产品需求文档

## 1. 产品定位
{context.get('title')} 是一个帮助非技术用户把业务需求转化为项目方案的工具。

## 2. 目标用户
运营人员、业务负责人、创业者、产品经理和技术团队。

## 3. 核心用户故事
- 作为业务人员，我可以用大白话描述需求
- 作为项目负责人，我可以上传资料并获得结构化分析
- 作为开发者，我可以获得技术方案和开发提示词

## 4. 功能范围
- 注册登录
- 项目空间
- 资料上传
- AI 分析
- 报告查看与导出

## 5. 验收标准
- 所有核心链路可在浏览器完成
- 报告内容可复制、可导出 Markdown
- 项目类型和状态可被清晰展示
""", {"report_type": "prd"}

    def generate_technical_solution(self, context: dict) -> tuple[str, dict]:
        return """# 技术方案

## 1. 架构概览
采用前后端分离架构：React 负责交互，FastAPI 负责业务 API，SQLite 负责 MVP 数据存储。

## 2. 后端模块
- 认证与 JWT
- 项目与消息管理
- 文件上传与类型识别
- AI Provider 抽象层
- 报告生成与导出

## 3. 数据库建议
使用 users、projects、project_assets、asset_analysis_results、project_reports 和 ai_task_logs 建立核心闭环。

## 4. API 建议
REST API 起步，后续可加入异步任务、WebSocket 状态推送和对象存储回调。

## 5. 扩展建议
未来可迁移 PostgreSQL，引入 Redis/Celery，并接入 OpenAI-compatible 多模型路由。
""", {"report_type": "technical_solution"}

    def generate_industry_solution(self, context: dict) -> tuple[str, dict]:
        return """# 行业解决方案

## 1. 目标
把当前业务问题拆解为可执行流程、人员分工、工具清单和风险控制方案。

## 2. 执行 SOP
1. 收集现有资料和流程
2. 标记关键节点和责任人
3. 设计标准表单与检查清单
4. 试运行 1 周并复盘
5. 固化为 SOP

## 3. 工具推荐
- 协作：飞书、Notion、企业微信
- 表格：Excel、Airtable、多维表格
- 自动化：Zapier、Make、低代码平台

## 4. 人员分工建议
- 业务负责人：确认目标和优先级
- 执行人员：记录流程和反馈问题
- 项目负责人：推进试点和复盘

## 5. 风险分析
- 流程规则不稳定
- 数据口径不一致
- 一线人员执行成本过高

## 6. 成本和周期估算
MVP 试点建议 1-2 周；正式推广需视团队规模扩展到 4-8 周。
""", {"report_type": "industry_solution"}

    def generate_developer_prompt(self, context: dict) -> tuple[str, dict]:
        from app.services.prompt_generator import generate_developer_prompt
        return generate_developer_prompt(context), {"report_type": "developer_prompt"}


def _truncate(value: Any, limit: int = 6000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def _read_file_preview(file_path: str, limit: int = 12000) -> str:
    try:
        return Path(file_path).read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _extract_first_json_object(text: str) -> dict | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class OpenAICompatibleProvider(BaseAIProvider):
    """OpenAI-compatible Chat Completions provider.

    The provider only keeps the API key server-side. Public errors and returned
    metadata intentionally omit authorization headers and key values.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model_text: str | None = None,
        model_vision: str | None = None,
        model_summary: str | None = None,
        model_code: str | None = None,
        model_industry: str | None = None,
        timeout_seconds: float | None = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.ai_base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key if api_key is not None else (settings.ai_api_key or "")
        self.model_text = model_text or settings.ai_model_text
        self.model_vision = model_vision or settings.ai_model_vision
        self.model_summary = model_summary or settings.ai_model_summary
        self.model_code = model_code or settings.ai_model_code
        self.model_industry = model_industry or settings.ai_model_industry
        self.timeout_seconds = timeout_seconds or settings.ai_timeout_seconds
        if not self.api_key.strip():
            raise AIProviderError("AI_API_KEY 为空，无法调用真实模型。")

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _sanitize_error(self, message: str) -> str:
        sanitized = message.replace(self.api_key, "[redacted]") if self.api_key else message
        return sanitized[:600]

    def _chat(self, model: str, messages: list[dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2400) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                response = client.post(self._endpoint(), headers=self._headers(), json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise AIProviderError(f"AI 模型调用超时（{self.timeout_seconds} 秒），请稍后重试或调大 AI_TIMEOUT_SECONDS。") from exc
        except httpx.HTTPStatusError as exc:
            body = self._sanitize_error(exc.response.text)
            raise AIProviderError(f"AI 模型服务返回错误：HTTP {exc.response.status_code}，{body}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"AI 模型网络请求失败：{self._sanitize_error(str(exc))}") from exc
        except json.JSONDecodeError as exc:
            raise AIProviderError("AI 模型服务返回了非 JSON 响应。") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("AI 模型服务响应格式不符合 OpenAI Chat Completions 规范。") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("AI 模型返回内容为空。")
        return content.strip()

    def _system_prompt(self, professional_level: str = "business") -> str:
        return f"""你是资深业务分析师、产品经理和技术方案顾问。请使用中文输出，专业程度为 {professional_level}。
要求：
- 输出 Markdown，结构清晰，可直接复制到项目文档。
- 不要编造无法从上下文推断的事实，缺失信息要列为待确认问题。
- 不要输出或复述任何 API Key、Authorization Header、系统环境变量值。
"""

    def _markdown_task(self, model: str, title: str, prompt: str, professional_level: str = "business", max_tokens: int = 2600) -> tuple[str, dict]:
        markdown = self._chat(
            model,
            [
                {"role": "system", "content": self._system_prompt(professional_level)},
                {"role": "user", "content": f"请生成《{title}》。\n\n{prompt}"},
            ],
            max_tokens=max_tokens,
        )
        return markdown, {"provider": "openai_compatible", "model": model, "report_type": title}

    def _json_task(self, model: str, title: str, prompt: str, max_tokens: int = 3200) -> tuple[str, dict]:
        content = self._chat(
            model,
            [
                {"role": "system", "content": self._system_prompt()},
                {
                    "role": "user",
                    "content": textwrap.dedent(
                        f"""
                        请完成《{title}》，必须只返回一个 JSON 对象，不要使用 Markdown 代码块。
                        JSON 格式：
                        {{
                          "markdown": "完整 Markdown 报告",
                          "structured": {{
                            "project_type": "software_development|office_automation|business_operation|data_analysis|consulting|unknown",
                            "requirement_score": 0-100,
                            "modules": ["模块1", "模块2"]
                          }}
                        }}

                        {prompt}
                        """
                    ).strip(),
                },
            ],
            max_tokens=max_tokens,
        )
        parsed = _extract_first_json_object(content)
        if not parsed:
            return content, {"provider": "openai_compatible", "model": model, **_json("unknown", 60)}
        markdown = str(parsed.get("markdown") or content).strip()
        structured = parsed.get("structured") if isinstance(parsed.get("structured"), dict) else {}
        project_type = structured.get("project_type", "unknown")
        if project_type not in _PROJECT_TYPES:
            project_type = "unknown"
        try:
            score = float(structured.get("requirement_score", 60))
        except (TypeError, ValueError):
            score = 60
        score = max(0, min(100, score))
        modules = structured.get("modules") if isinstance(structured.get("modules"), list) else []
        return markdown, {
            "provider": "openai_compatible",
            "model": model,
            "project_type": project_type,
            "requirement_score": score,
            "modules": modules,
        }

    def analyze_text(self, text: str, professional_level: str = "business") -> tuple[str, dict]:
        prompt = f"""资料正文：
{_truncate(text, 12000)}

请从用户目标、业务场景、已识别需求、潜在功能模块、待确认问题和风险提示进行分析。"""
        markdown, data = self._json_task(self.model_text, "文本资料分析报告", prompt)
        data["analyzer_type"] = "text_ai"
        return markdown, data

    def analyze_image(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        mime_type = mimetypes.guess_type(file_path)[0] or "image/png"
        try:
            image_data = base64.b64encode(Path(file_path).read_bytes()).decode("ascii")
        except OSError as exc:
            raise AIProviderError("图片文件读取失败，无法提交给视觉模型。") from exc
        content = [
            {"type": "text", "text": "请分析这张图片中的业务线索、页面/流程信息、可转化需求和待确认问题，输出 Markdown。"},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
        ]
        markdown = self._chat(
            self.model_vision,
            [{"role": "system", "content": self._system_prompt(professional_level)}, {"role": "user", "content": content}],
            max_tokens=2200,
        )
        return markdown, {"provider": "openai_compatible", "model": self.model_vision, "analyzer_type": "vision_ai", **_json()}

    def analyze_audio(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        prompt = f"文件路径/名称：{Path(file_path).name}\n当前仅通过 Chat Completions 接口处理，请基于文件名和项目上下文给出音频资料分析模板、建议转写字段、待确认问题。"
        return self._markdown_task(self.model_text, "音频资料分析报告", prompt, professional_level)

    def analyze_video(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        prompt = f"文件路径/名称：{Path(file_path).name}\n当前仅通过 Chat Completions 接口处理，请给出视频资料分析模板、建议抽帧/转写字段、流程拆解方法和待确认问题。"
        return self._markdown_task(self.model_text, "视频资料分析报告", prompt, professional_level)

    def analyze_table(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        preview = _read_file_preview(file_path)
        prompt = f"文件名：{Path(file_path).name}\n表格文本预览：\n{_truncate(preview or '无法直接读取表格内容，请根据文件名给出分析框架。', 12000)}"
        return self._json_task(self.model_summary, "表格资料分析报告", prompt)

    def analyze_document(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        preview = _read_file_preview(file_path)
        prompt = f"文件名：{Path(file_path).name}\n文档文本预览：\n{_truncate(preview or '无法直接读取文档内容，请根据文件名给出分析框架。', 12000)}"
        return self._json_task(self.model_summary, "文档资料分析报告", prompt)

    def analyze_code(self, file_path: str, professional_level: str = "business") -> tuple[str, dict]:
        preview = _read_file_preview(file_path)
        prompt = f"文件名：{Path(file_path).name}\n代码预览：\n{_truncate(preview or '无法读取代码内容。', 14000)}\n请分析用途、框架/依赖线索、风险、重构建议和可落地任务。"
        return self._json_task(self.model_code, "代码资料分析报告", prompt)

    def comprehensive_analysis(self, context: dict) -> tuple[str, dict]:
        prompt = f"""项目上下文 JSON：
{_truncate(context, 18000)}

请输出项目综合分析报告，并判断 project_type 与 requirement_score。"""
        return self._json_task(self.model_summary, "项目综合分析报告", prompt)

    def generate_prd(self, context: dict) -> tuple[str, dict]:
        prompt = f"项目上下文 JSON：\n{_truncate(context, 18000)}\n请生成包含产品定位、目标用户、用户故事、功能范围、验收标准、里程碑和待确认问题的 PRD。"
        md, data = self._markdown_task(self.model_summary, "PRD 产品需求文档", prompt, max_tokens=3200)
        data["report_type"] = "prd"
        return md, data

    def generate_technical_solution(self, context: dict) -> tuple[str, dict]:
        prompt = f"项目上下文 JSON：\n{_truncate(context, 18000)}\n请生成技术方案，覆盖架构、模块、数据库、API、部署、风险和后续扩展。"
        md, data = self._markdown_task(self.model_code, "技术方案", prompt, max_tokens=3200)
        data["report_type"] = "technical_solution"
        return md, data

    def generate_industry_solution(self, context: dict) -> tuple[str, dict]:
        prompt = f"项目上下文 JSON：\n{_truncate(context, 18000)}\n请生成行业解决方案，覆盖目标、SOP、工具、人员分工、风险、成本周期。"
        md, data = self._markdown_task(self.model_industry, "行业解决方案", prompt, max_tokens=3200)
        data["report_type"] = "industry_solution"
        return md, data

    def generate_developer_prompt(self, context: dict) -> tuple[str, dict]:
        prompt = f"项目上下文 JSON：\n{_truncate(context, 18000)}\n请生成可直接交给 AI 编程工具使用的开发提示词，包含目标、技术栈、数据模型、接口、页面、验收标准和约束。"
        md, data = self._markdown_task(self.model_code, "AI 编程工具开发提示词", prompt, max_tokens=3600)
        data["report_type"] = "developer_prompt"
        return md, data


def create_ai_provider() -> BaseAIProvider:
    settings = get_settings()
    if settings.ai_api_key and settings.ai_api_key.strip():
        return OpenAICompatibleProvider()
    return MockAIProvider()
