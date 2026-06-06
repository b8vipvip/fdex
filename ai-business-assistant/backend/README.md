# 后端服务

FastAPI 后端负责认证、项目管理、文件上传、文件类型识别、数据隐私检测、AI Provider 分析、报告生成和 Markdown 导出。

## 启动

```bash
cd ai-business-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

默认地址：http://localhost:8000  
API 文档：http://localhost:8000/docs

## 环境变量

见 `.env.example`。`AI_API_KEY` 为空时自动回退到 `MockAIProvider`；填写 `AI_API_KEY` 后使用 `OpenAICompatibleProvider` 调用 OpenAI-compatible Chat Completions API。API Key 只在服务端读取，不会通过接口返回给前端。

```env
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=your-api-key
AI_MODEL_TEXT=gpt-4o-mini
AI_MODEL_VISION=gpt-4o-mini
AI_MODEL_SUMMARY=gpt-4o-mini
AI_MODEL_CODE=gpt-4o-mini
AI_MODEL_INDUSTRY=gpt-4o-mini
AI_TIMEOUT_SECONDS=60
```

模型用途：

- `AI_MODEL_TEXT`：文本资料分析。
- `AI_MODEL_VISION`：图片资料分析。
- `AI_MODEL_SUMMARY`：综合分析与 PRD。
- `AI_MODEL_CODE`：代码资料、技术方案和开发提示词。
- `AI_MODEL_INDUSTRY`：行业解决方案。

所有 AI 调用都会记录到 `ai_task_logs`，包括运行状态、输入摘要、输出摘要和脱敏错误信息。

## 隐私与存储模式

`projects` 表包含 `storage_mode`、`data_retention_policy`、`allow_third_party_ai`、`auto_desensitize`；`project_assets` 表包含 `privacy_level`、`is_sensitive`、`desensitized_path`、`original_deleted_at`、`retention_deadline`。应用启动时会执行轻量字段迁移，补齐旧 SQLite 数据库缺失列。

存储模式：

- `local_only`：后端不保存原始文件，只保留资料元信息，前端提示当前设备本地保存且不同步。
- `cloud`：原始文件保存到云端，支持多端同步；高敏文件会返回提醒状态。
- `hybrid`：敏感资料进入 `need_user_decision`，用户通过 `POST /api/assets/{asset_id}/privacy-decision` 选择脱敏、临时分析、仅本地或确认上传。
- `temporary`：分析后删除原始文件并更新 `original_deleted_at`，保留分析结果和报告。

`privacy_service.py` 使用正则和关键词检测手机号、邮箱、身份证、银行卡、API Key、Token、Cookie、密码、secret、access_key、private_key、地址、合同、财务和客户名单等信息。日志和 `ai_task_logs` 会尽量写入脱敏摘要；关闭 `allow_third_party_ai` 时分析服务会使用 `MockAIProvider`，不会把资料发送给第三方 AI。

新增接口：

- `GET /api/projects/{project_id}/privacy-summary`
- `POST /api/assets/{asset_id}/privacy-decision`

## 数据库

默认使用 SQLite：`ai_business_assistant.db`。应用启动时会自动初始化表，无需手动迁移。

## 上传目录

默认使用 `uploads/`。应用启动时自动创建目录；每个项目的文件会保存到 `uploads/{project_id}/`。

## 烟测

```bash
cd ai-business-assistant/backend
source .venv/bin/activate
# 以下两种运行方式任选其一
python scripts/smoke_test.py
PYTHONPATH=. python scripts/smoke_test.py
```

烟测会使用临时 SQLite 数据库和上传目录，覆盖注册登录、JWT、项目创建、文件上传、资料分析、综合分析、多文档生成和 Markdown 导出。

## 主要接口

- `GET /api/health`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET/POST /api/projects`
- `GET/PUT/DELETE /api/projects/{project_id}`
- `POST/GET /api/projects/{project_id}/messages`
- `POST /api/projects/{project_id}/assets/upload`
- `POST /api/projects/{project_id}/assets/local-only`
- `GET /api/projects/{project_id}/assets`
- `POST /api/assets/{asset_id}/privacy-decision`
- `POST /api/assets/{asset_id}/analyze`
- `POST /api/projects/{project_id}/analyze`
- `GET /api/projects/{project_id}/privacy-summary`
- `GET /api/projects/{project_id}/reports`
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/export-md`
