from abc import ABC, abstractmethod


def _json(project_type: str = "business_operation", score: float = 72) -> dict:
    return {"project_type": project_type, "requirement_score": score, "modules": ["需求收集", "资料分析", "方案生成", "文档导出"]}


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


class OpenAICompatibleProvider(BaseAIProvider):
    """预留真实 OpenAI-compatible API 接入结构。"""
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def _todo(self):
        raise NotImplementedError("TODO: 接入 OpenAI-compatible chat/completions 或 responses API")

    analyze_text = analyze_image = analyze_audio = analyze_video = analyze_table = analyze_document = analyze_code = comprehensive_analysis = generate_prd = generate_technical_solution = generate_industry_solution = generate_developer_prompt = lambda self, *args, **kwargs: self._todo()
