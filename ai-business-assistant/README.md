# AI业务落地助手 MVP

面向非技术用户的 AI 业务落地平台。用户可以用大白话描述需求、上传多类型资料，系统识别资料类型并通过 MockAIProvider 模拟文本、图片、表格、代码等分析器输出单资料报告，再生成综合分析报告、开发类文档或行业落地方案。

## 已实现功能

- 用户注册、登录、JWT 鉴权
- 项目空间管理：列表、新建、详情、删除接口预留
- 文本需求输入和项目消息记录
- 文件上传、本地 uploads 存储、50MB 限制
- 文件类型识别：文本、图片、音频、视频、表格、文档、代码、压缩包、未知
- AI 路由分发与 MockAIProvider 模拟分析
- 单资料分析结果、综合分析报告、PRD、技术方案、行业方案、SOP、风险报告、开发提示词
- Markdown 文档查看、复制、导出
- SQLite 自动建表、AI 任务日志记录
- React 响应式 Web，PC 和手机浏览器可使用，同账号数据同步

## 技术栈

- 前端：React + TypeScript + Vite + Tailwind CSS + Zustand + Axios
- 后端：Python FastAPI + SQLAlchemy + SQLite + JWT + Pydantic
- AI：MockAIProvider + OpenAICompatibleProvider 预留接口

## 快速启动

### 后端

```bash
cd ai-business-assistant/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

后端默认地址：http://localhost:8000
API 文档：http://localhost:8000/docs

### 前端

```bash
cd ai-business-assistant/frontend
npm install
npm run dev
```

前端默认地址：http://localhost:5173

## 验收流程

1. 启动后端和前端
2. 打开前端页面并注册用户
3. 登录后新建项目
4. 输入一段大白话需求
5. 上传 txt、图片或 Excel 文件
6. 在项目详情页点击“分析资料”
7. 点击“综合分析项目”或“生成文档”
8. 打开报告列表中的文档
9. 点击复制或导出 Markdown

## 后续建议

- 接入真实 OpenAI-compatible 多模型 Provider
- 引入 Celery/RQ 异步任务和 WebSocket 状态推送
- SQLite 迁移到 PostgreSQL 或 MySQL
- 本地 uploads 替换为 S3/OSS/COS 对象存储
- 增加团队协作、模板库、行业知识库和权限系统
