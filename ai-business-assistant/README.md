# AI业务落地助手 MVP

面向非技术用户的 AI 业务落地平台。用户可以用大白话描述需求、上传多类型资料，系统识别资料类型并通过 `MockAIProvider` 模拟文本、图片、表格、代码等分析器输出单资料报告，再生成综合分析报告、开发类文档或行业落地方案。

## 已实现功能

- 用户注册、登录、JWT 鉴权
- 项目空间管理：列表、新建、详情、删除接口预留
- 文本需求输入和项目消息记录
- 文件上传、本地 `uploads` 存储、50MB 限制
- 文件类型识别：文本、图片、音频、视频、表格、文档、代码、压缩包、未知
- AI 路由分发与 `MockAIProvider` 模拟分析
- 单资料分析结果、综合分析报告、PRD、技术方案、行业方案、SOP、风险报告、开发提示词
- Markdown 文档查看、复制、导出
- SQLite 自动建表、AI 任务日志记录
- React 响应式 Web，PC 和手机浏览器可使用，同账号数据同步

## 技术栈

- 前端：React + TypeScript + Vite + Tailwind CSS + Zustand + Axios
- 后端：Python FastAPI + SQLAlchemy + SQLite + JWT + Pydantic
- AI：`MockAIProvider` + `OpenAICompatibleProvider` 预留接口

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

## 后端烟测脚本

安装后端依赖后，可以运行一次完整 API 烟测。脚本会使用临时 SQLite 数据库和临时上传目录，不污染本地开发数据。

```bash
cd ai-business-assistant/backend
source .venv/bin/activate
python scripts/smoke_test.py
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

后端环境变量模板位于 `backend/.env.example`。第一版使用 `MockAIProvider`，不会真实调用外部 AI。后续可通过 `AI_BASE_URL`、`AI_API_KEY` 和模型配置接入 OpenAI-compatible API。

前端只需要在调用非默认后端地址时设置：

```bash
VITE_API_BASE_URL=http://localhost:8000/api
```

## 后续建议

- 接入真实 OpenAI-compatible 多模型 Provider
- 引入 Celery/RQ 异步任务和 WebSocket 状态推送
- SQLite 迁移到 PostgreSQL 或 MySQL
- 本地 uploads 替换为 S3/OSS/COS 对象存储
- 增加团队协作、模板库、行业知识库和权限系统
