# 后端服务

FastAPI 后端负责认证、项目管理、文件上传、文件类型识别、Mock AI 分析、报告生成和 Markdown 导出。

## 启动

```bash
cd ai-business-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## 环境变量

见 `.env.example`。第一版使用 `MockAIProvider`，不会真实调用外部 AI。后续可通过 `AI_BASE_URL`、`AI_API_KEY` 和模型配置接入 OpenAI-compatible API。

## 数据库

默认使用 SQLite：`ai_business_assistant.db`。应用启动时自动初始化表。

## 主要接口

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET/POST /api/projects`
- `GET/PUT/DELETE /api/projects/{project_id}`
- `POST/GET /api/projects/{project_id}/messages`
- `POST /api/projects/{project_id}/assets/upload`
- `GET /api/projects/{project_id}/assets`
- `POST /api/assets/{asset_id}/analyze`
- `POST /api/projects/{project_id}/analyze`
- `GET /api/projects/{project_id}/reports`
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/export-md`
