# AI业务落地助手 MVP

面向非技术用户的 AI 业务落地平台。用户可以用大白话描述需求、上传多类型资料，系统识别资料类型并通过 AI Provider 输出单资料报告，再生成综合分析报告、开发类文档或行业落地方案。未配置 `AI_API_KEY` 时使用 `MockAIProvider`，配置后使用 `OpenAICompatibleProvider` 调用真实 OpenAI-compatible Chat Completions API。

## 已实现功能

- 用户注册、登录、JWT 鉴权
- 项目空间管理：列表、新建、详情、删除接口预留
- 文本需求输入和项目消息记录
- 文件上传、本地 `uploads` 存储、50MB 限制，支持隐私检测与多种存储模式
- 文件类型识别：文本、图片、音频、视频、表格、文档、代码、压缩包、未知
- AI 路由分发；支持 `MockAIProvider` 自动兜底和 `OpenAICompatibleProvider` 真实模型调用
- 单资料分析结果、综合分析报告、PRD、技术方案、行业方案、SOP、风险报告、开发提示词
- Markdown 文档查看、复制、导出
- SQLite 自动建表、轻量字段迁移、AI 任务日志记录
- React 响应式 Web，PC 和手机浏览器可使用，同账号数据同步

## 技术栈

- 前端：React + TypeScript + Vite + Tailwind CSS + Zustand + Axios
- 后端：Python FastAPI + SQLAlchemy + SQLite + JWT + Pydantic
- AI：`MockAIProvider` + `OpenAICompatibleProvider`（OpenAI-compatible Chat Completions API）

## 目录结构

```text
ai-business-assistant/
├── backend/      # FastAPI API、SQLite 数据库、文件上传、Mock AI 分析
└── frontend/     # React + Vite Web 前端
```

## 快速启动

> 建议使用两个终端分别启动后端和前端。下面命令均从仓库根目录 `/workspace/fdex` 执行。

### 1. 启动后端

```bash
cd ai-business-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

后端默认地址：http://localhost:8000  
API 文档：http://localhost:8000/docs

### 2. 启动前端

```bash
cd ai-business-assistant/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

前端默认地址：http://localhost:5173

如需修改前端调用的后端地址：

```bash
VITE_API_BASE_URL=http://localhost:8000/api npm run dev -- --host 0.0.0.0 --port 5173
```

## 服务器部署

以下示例假设前端通过 `http://服务器IP:5173` 访问，后端使用 `8001` 端口。请将命令中的 `服务器IP` 替换为服务器实际公网 IP 或域名，并在防火墙或安全组中放行 `5173` 和 `8001` 端口。

### 1. 配置并启动后端

```bash
cd ai-business-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑后端 `.env`，至少确认密钥和 CORS 配置。公网部署时，`CORS_ORIGINS` 必须包含实际前端访问地址：

```dotenv
SECRET_KEY=请替换为安全的随机密钥
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://服务器IP:5173
```

使用 `8001` 端口启动后端：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

后端健康检查地址为 `http://服务器IP:8001/api/health`，API 文档地址为 `http://服务器IP:8001/docs`。

### 2. 配置、构建并启动前端

在前端目录创建 `.env.local`，让浏览器访问服务器的 `8001` 端口：

```bash
cd ai-business-assistant/frontend
printf 'VITE_API_BASE_URL=http://服务器IP:8001/api\n' > .env.local
npm install
npm run build
npm run dev -- --host 0.0.0.0 --port 5173
```

生产环境可以使用 Nginx 等静态文件服务器托管 `frontend/dist/`。`.env` 和 `.env.local` 包含部署环境配置，不要提交到 Git。

### 3. 运行后端烟测

安装后端依赖并进入 `backend` 目录后，以下两种运行方式都应可用：

```bash
cd ai-business-assistant/backend
source .venv/bin/activate
python scripts/smoke_test.py
PYTHONPATH=. python scripts/smoke_test.py
```

烟测使用临时 SQLite 数据库和临时上传目录，不会污染部署数据。

## 数据库与上传目录

- 默认数据库：`ai-business-assistant/backend/ai_business_assistant.db`
- 默认上传目录：`ai-business-assistant/backend/uploads/`
- 后端启动时会自动执行 `Base.metadata.create_all(...)` 创建缺失表，并自动创建上传目录。
- MVP 阶段不需要手动执行迁移命令；如需重置本地数据，可在停止后端后删除 `ai_business_assistant.db` 和 `uploads/` 中的测试文件。

## 完整验收流程

1. 启动后端，确认 `GET http://localhost:8000/api/health` 返回 `{"status":"ok"}`。
2. 启动前端并打开 http://localhost:5173。
3. 注册新用户。注册成功后前端会把 JWT 保存到 `localStorage.token`。
4. 刷新页面或进入任意项目接口时，前端 Axios 拦截器会自动携带 `Authorization: Bearer <token>`。
5. 新建项目，填写大白话需求。
6. 在项目详情页上传 txt、图片、PDF、Word、Excel、音频、视频或代码文件；上传成功后资料列表会自动刷新。
7. 点击“分析资料”，生成单资料分析结果并刷新资料状态。
8. 点击“综合分析项目”或“生成文档”，生成综合分析和多个最终文档。
9. 打开任意报告，点击“复制内容”或“导出 Markdown”。

## 数据隐私与存储模式

项目创建时可以选择 `storage_mode`，默认是 `hybrid`：

- `local_only` 本地模式：后端不保存原始文件，第一版 Web MVP 仅提示使用浏览器本地存储占位，换设备不同步；如需云端 AI 分析，需要改选临时上传或脱敏上传。
- `cloud` 云端模式：资料上传到后端存储，支持多端同步和完整 AI 分析；如果检测到高敏信息，会在资料状态中给出提醒。
- `hybrid` 混合模式：非敏感资料直接云端保存；敏感资料进入 `need_user_decision` 状态，用户可选择自动脱敏、临时分析后删除、仅本地保存或确认上传原文。
- `temporary` 临时分析模式：资料上传后仅用于本次分析，分析完成后删除原始文件，只保留分析报告并写入 `original_deleted_at`。

项目还支持：

- `data_retention_policy`：`keep_forever`、`delete_after_analysis`、`delete_after_1_day`、`delete_after_7_days`、`delete_after_30_days`。
- `allow_third_party_ai`：关闭后不会调用 `OpenAICompatibleProvider`，会回退到 `MockAIProvider`，后续可接入 LocalAIProvider。
- `auto_desensitize`：开启后文本类敏感资料会生成脱敏副本，AI 分析优先使用脱敏内容。

敏感检测第一版使用正则和关键词，覆盖手机号、邮箱、身份证、银行卡、API Key、Token、Cookie、密码、secret、access_key、private_key、地址、合同、财务和客户名单关键词。检测结果只保存类型、数量和脱敏示例，不在日志或 `ai_task_logs` 中记录原始敏感内容。新增接口：

- `GET /api/projects/{project_id}/privacy-summary`：查看项目隐私设置、敏感文件数量、待处理文件数量和保留策略。
- `POST /api/assets/{asset_id}/privacy-decision`：对敏感资料选择 `desensitize`、`temporary`、`local_only` 或 `confirm_upload`。

## 后端烟测脚本

安装后端依赖后，可以运行一次完整 API 烟测。脚本会使用临时 SQLite 数据库和临时上传目录，不污染本地开发数据。

```bash
cd ai-business-assistant/backend
source .venv/bin/activate
# 以下两种运行方式任选其一
python scripts/smoke_test.py
PYTHONPATH=. python scripts/smoke_test.py
```

该脚本覆盖：健康检查、数据库自动建表、注册、登录、JWT 鉴权、创建项目、文件上传、资料列表刷新对应接口、单资料分析、综合分析、多报告生成、Markdown 导出。

## 常用检查命令

### 后端 Python 语法检查

```bash
cd ai-business-assistant/backend
python -m compileall -q app scripts
```

### 前端 TypeScript / 构建检查

```bash
cd ai-business-assistant/frontend
npm run build
```

## 环境变量

后端环境变量模板位于 `backend/.env.example`。`AI_API_KEY` 为空时自动使用 `MockAIProvider`，不会真实调用外部 AI；填写后会使用 `OpenAICompatibleProvider` 调用 OpenAI-compatible Chat Completions API。API Key 仅在后端读取，不会返回给前端。

常用 AI 配置：

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

模型用途：文本资料使用 `AI_MODEL_TEXT`，图片资料使用 `AI_MODEL_VISION`，综合分析与 PRD 使用 `AI_MODEL_SUMMARY`，技术方案和开发提示词使用 `AI_MODEL_CODE`，行业方案使用 `AI_MODEL_INDUSTRY`。所有 AI 调用都会写入 `ai_task_logs`，失败时记录脱敏后的错误信息。

前端只需要在调用非默认后端地址时设置：

```bash
VITE_API_BASE_URL=http://localhost:8000/api
```

## 后续建议

- 为 OpenAI-compatible Provider 增加流式输出、异步任务队列和模型可用性检查
- 引入 Celery/RQ 异步任务和 WebSocket 状态推送
- SQLite 迁移到 PostgreSQL 或 MySQL
- 本地 uploads 替换为 S3/OSS/COS 对象存储
- 增加团队协作、模板库、行业知识库和权限系统
