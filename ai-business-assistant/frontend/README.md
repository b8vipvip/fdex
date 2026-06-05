# 前端应用

React + TypeScript + Vite + Tailwind CSS 响应式 Web 应用，提供登录、注册、项目列表、新建项目、项目详情、文件上传、分析触发、报告查看和 Markdown 导出。

## 启动

```bash
cd ai-business-assistant/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

默认访问：http://localhost:5173

如需修改后端地址，可设置：

```bash
VITE_API_BASE_URL=http://localhost:8000/api npm run dev -- --host 0.0.0.0 --port 5173
```

## 构建检查

```bash
cd ai-business-assistant/frontend
npm run build
```

## 前后端联调说明

- 登录和注册成功后，JWT 会保存到 `localStorage.token`。
- 前端 Axios 客户端会为后续 API 请求自动添加 `Authorization: Bearer <token>`。
- 项目详情页上传文件成功后会重新请求资料列表、报告列表和消息列表，确保上传后的资料立即显示。
- 点击“分析资料”后会刷新资料状态；点击“综合分析项目”或“生成文档”后会刷新报告列表。
- 报告详情页导出 Markdown 时会通过 `fetch` 携带同一个 JWT。
