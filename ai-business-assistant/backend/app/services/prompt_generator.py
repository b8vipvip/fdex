def generate_developer_prompt(context: dict) -> str:
    title = context.get("title", "未命名项目")
    project_type = context.get("project_type", "unknown")
    description = context.get("description", "")
    modules = context.get("modules", ["用户认证", "项目管理", "资料上传", "AI 分析", "报告导出"])
    return f"""# 给 AI 编程工具的开发提示词

你是资深全栈工程师，请根据以下需求开发项目：

## 项目背景
项目名称：{title}
项目类型：{project_type}
业务描述：{description or '用户希望把模糊业务需求转化为可执行方案。'}

## 用户目标
- 降低业务人员与技术团队沟通成本
- 快速形成可评审、可开发、可验收的文档
- 优先交付 MVP，后续逐步扩展

## 已确认需求
- 支持用户注册登录与个人项目空间
- 支持文本需求输入、文件上传、资料分析和综合报告生成
- 支持开发类与非开发类输出路径

## 待确认问题
- 是否需要团队协作、审批流、付费套餐
- 是否需要接入真实大模型和对象存储
- 是否有行业数据合规要求

## 功能模块
{chr(10).join(f'- {item}' for item in modules)}

## 技术栈建议
- 前端：React + TypeScript + Vite + Tailwind CSS
- 后端：FastAPI + SQLAlchemy + SQLite/PostgreSQL
- AI：OpenAI-compatible Provider 抽象层

## 数据库设计建议
- users：用户与专业层级
- projects：项目基础信息、类型和状态
- project_assets：上传资料与类型识别
- asset_analysis_results：单资料分析结果
- project_reports：综合报告、PRD、技术方案、行业方案

## API设计建议
- /api/auth：注册、登录、当前用户
- /api/projects：项目 CRUD 与项目消息
- /api/assets：上传、查询、触发分析
- /api/reports：报告查看与 Markdown 导出

## 页面设计建议
- 登录/注册页
- 项目列表页
- 新建项目页
- 项目详情页
- 文档查看页
- 设置页

## 开发阶段
1. 完成数据库模型和 API 骨架
2. 完成前端页面和认证流程
3. 完成 MockAI 分析链路
4. 完成文档展示、复制、导出
5. 接入真实 AI Provider 与部署

## 验收标准
- 用户可注册登录并新建项目
- 可上传资料并识别文件类型
- 可生成资料分析报告和综合报告
- 开发类项目可生成 PRD、技术方案和开发提示词
- 非开发类项目可生成行业方案、SOP 和风险报告

## 注意事项
- 不要硬编码密钥
- 上传文件限制大小并记录日志
- API 返回错误要清晰
- 前后端类型保持一致
"""
