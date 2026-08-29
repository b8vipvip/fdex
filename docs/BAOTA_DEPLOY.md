# 宝塔面板部署 FDEX 服务端

目标结构：

```text
/opt/fdex/                  # Git 仓库
/opt/fdex/server/.env       # 服务端私密配置，不提交 Git
/opt/fdex/server/data/      # 审计日志和 .env 备份
/etc/systemd/system/fdex.service
宝塔网站：fdex.k2n.cn -> 127.0.0.1:18080
```

FDEX 默认使用独立端口 `18080`，只监听本机，不需要开放服务器安全组。

## 一、部署或更新

唯一权威的仓库内更新入口是：

```bash
cd /opt/fdex
sudo bash /opt/fdex/scripts/update_server.sh
```

历史服务器上可能还保留 `/opt/deploy_fdex.sh`。新版 FDEX 会在 `fdex.service` 启动前自动把它修复为仓库内 `scripts/deploy_fdex_compat.sh` 的兼容入口，最终仍转到同一个 `scripts/update_server.sh`。不要继续维护一份独立的 `/opt/deploy_fdex.sh` 部署逻辑。

如果服务器仍处于旧版本，而且 `/opt/deploy_fdex.sh` 报错引用已经删除的 `ai-business-assistant/backend`，先执行一次仓库内权威入口：

```bash
cd /opt/fdex
git fetch origin main
git checkout main
git reset --hard origin/main
sudo bash /opt/fdex/scripts/update_server.sh
```

成功重启后，`/opt/deploy_fdex.sh` 会自动被修复，后续继续使用旧命令也会进入权威更新器。

脚本会自动：

1. 备份并恢复现有 `server/.env`
2. 拉取 GitHub `main`
3. 初始化缺失的管理员密码和会话密钥
4. 检查目标端口，且不会结束占用端口的其他服务
5. 更新 Python 依赖
6. 启动/检查长期记忆栈；memory-provider-proxy 使用轻量专用镜像，不安装 Codex/完整服务端依赖
7. 安装 systemd 服务、重启 FDEX 并检查健康接口

后台更新进度到 `70%` 时对应长期记忆栈阶段。Docker 首次拉取 Qdrant/Letta 镜像仍可能耗时，但 memory-provider-proxy 不再重复安装完整 FDEX/Codex 依赖，而且 Docker 构建/启动有总超时、后续健康检查也有固定次数上限。默认 `FDEX_MEMORY_REQUIRED=false` 时，记忆栈失败或超时会 fail-open，核心 FDEX 服务继续完成更新；只有显式设置为 `true` 才会因为记忆服务失败停止部署。

首次生成管理员密码时，终端会显示：

```text
管理后台：https://fdex.k2n.cn/admin
管理员用户名：admin
首次生成的管理员密码：随机密码
```

该密码只在生成时显示一次。登录后应立即在“服务配置 → 修改管理员密码”中更换。

## 二、宝塔反向代理

在宝塔面板打开：

```text
网站 -> fdex.k2n.cn -> 设置 -> 反向代理
```

配置：

```text
代理目录：/
目标 URL：http://127.0.0.1:18080
发送域名：$host
```

根目录 `/` 必须代理，这样以下地址都会转发到 FastAPI：

```text
/                       管理后台入口
/admin                  服务端管理后台
/static                 后台静态资源
/docs                   Swagger API 文档
/openapi.json            OpenAPI 定义
/api/*                   Android 与公开 API
```

不再需要旧目录：

```text
/opt/fdex/ai-business-assistant/frontend/dist
```

站点配置中可以把旧的 `root` 改为普通空目录，例如：

```nginx
root /www/wwwroot/fdex.k2n.cn;
```

宝塔反向代理生成的规则已包含 `location /` 时，不要再手工添加重复的 `location /api/`、`location /docs` 或 `location /openapi.json`。

完成后开启 SSL 和强制 HTTPS。

## 三、服务端配置

配置文件：

```text
/opt/fdex/server/.env
```

主要配置：

```dotenv
APP_NAME=FDEX Server
APP_VERSION=1.0.0
ENVIRONMENT=production
PUBLIC_BASE_URL=https://fdex.k2n.cn
API_PREFIX=/api
CORS_ORIGINS=https://fdex.k2n.cn

FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2

AI_PROVIDER=openai_compatible
AI_BASE_URL=https://你的接口地址/v1
AI_API_KEY=你的密钥
AI_MODEL=你的模型名称
AI_TIMEOUT_SECONDS=60

ADMIN_USERNAME=admin
ADMIN_PASSWORD=至少12位强密码
ADMIN_SESSION_SECRET=至少32位随机字符串
ADMIN_COOKIE_SECURE=true
ADMIN_SESSION_HOURS=12
```

这些内容可以在管理后台可视化修改。API Key 在页面中只显示脱敏结果，留空表示保持当前密钥。

长期记忆 Docker 构建/启动默认最多等待 900 秒。只有在网络环境确实需要更长时间时才建议在 `server/.env` 增加：

```dotenv
FDEX_MEMORY_SETUP_TIMEOUT_SECONDS=1200
```

允许范围为 60–3600 秒。

## 四、端口冲突处理

检查端口：

```bash
ss -lntp 'sport = :18080'
```

如果被占用，在 `.env` 中修改：

```dotenv
FDEX_PORT=18081
```

并把宝塔目标 URL 同步改为：

```text
http://127.0.0.1:18081
```

更新脚本只会停止 `fdex.service`，不会结束占用目标端口的其他程序。

## 五、管理后台

访问：

```text
https://fdex.k2n.cn/admin
```

功能：

- 服务、主机、资源和 GitHub 状态仪表盘
- 服务地址、API 路由、CORS、端口和工作进程配置
- AI Provider、接口地址、密钥和模型配置
- AI 接口连通性测试
- systemd 日志和管理员审计日志
- GitHub main/Release 检查
- 服务重启和后台更新
- 管理员密码修改

后台安全措施：Secure/HttpOnly Cookie、CSRF Token、登录失败限速、配置原子写入、自动备份和操作审计。

## 六、验证

```bash
curl http://127.0.0.1:18080/api/health
curl https://fdex.k2n.cn/api/health
```

浏览器：

```text
https://fdex.k2n.cn/admin
https://fdex.k2n.cn/docs
```

## 七、常用维护命令

```bash
systemctl status fdex
systemctl restart fdex
journalctl -u fdex -n 100 --no-pager
journalctl -u fdex -f
```

Android 默认访问 `https://fdex.k2n.cn`。第三方 API Key 只保存在服务端，不要放进 Android 工程或 GitHub 仓库。
