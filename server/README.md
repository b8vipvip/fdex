# FDEX Server

FDEX FastAPI 服务端，同时提供 Android API、Swagger 文档和中文管理后台。

## 管理后台

部署后访问：

```text
https://fdex.k2n.cn/admin
```

后台包含：

- 管理员登录、CSRF 防护和登录限速
- 服务、主机、资源与 GitHub 状态仪表盘
- AI Provider、Base URL、API Key 和模型配置
- AI 接口连通性测试
- systemd 日志和管理员审计日志
- 服务重启与 GitHub main 更新
- 管理员密码修改

首次运行 `scripts/update_server.sh` 时，脚本会自动生成安全的管理员密码和会话密钥，并在当前终端显示一次初始密码。

## 配置

```bash
cp .env.example .env
```

默认仅监听本机：

```dotenv
FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2
```

管理后台安全设置：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=至少12位的强密码
ADMIN_SESSION_SECRET=至少32位随机字符串
ADMIN_COOKIE_SECURE=true
ADMIN_SESSION_HOURS=12
```

第三方 AI 密钥只保存在 `server/.env`，公开 API 和后台 HTML 都不会返回完整密钥。后台保存配置时会原子写入 `.env`，并在 `server/data/backups/` 创建权限为 600 的备份。

端口被占用时，修改 `FDEX_PORT`，不要结束不属于 FDEX 的进程。

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

健康检查：

```bash
curl http://127.0.0.1:18080/api/health
```

## 主要地址

```text
/                       跳转管理后台
/admin                  管理后台
/docs                   Swagger API 文档
/api/info               服务信息
/api/health             健康检查
/api/version            服务版本
/api/public-config      Android 可读取的非敏感配置
```

## 测试

```bash
PYTHONPATH=. pytest -q
```
