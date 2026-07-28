# FDEX

FDEX 是 **Android 原生客户端 + FastAPI 服务端 + 中文管理后台**。旧的 `ai-business-assistant/` Web 客户端已经删除，不兼容旧版数据或接口。

## 目录结构

```text
.
├── app/                    # Android 客户端（Kotlin + Jetpack Compose）
├── server/                 # FastAPI、管理后台和 Android API
├── deploy/                 # systemd 与宝塔配置
├── scripts/                # 服务端更新脚本
├── docs/                   # 部署说明
└── .github/workflows/      # GitHub Actions 构建与发布
```

## Android 客户端

- 最低支持 Android 8.0（API 26）
- “设置 → 关于”显示版本名、版本号和构建提交
- 启动时自动检查 GitHub Release，也可手动检查
- 发现新版本后下载 APK 并调用系统安装器
- 默认通过 `https://fdex.k2n.cn` 访问 FDEX 服务端

本地构建：

```bash
gradle :app:assembleDebug
```

APK：

```text
app/build/outputs/apk/debug/app-debug.apk
```

## 服务端与管理后台

服务器更新：

```bash
cd /opt/fdex
sudo bash scripts/update_server.sh
```

默认监听：

```text
127.0.0.1:18080
```

宝塔站点 `fdex.k2n.cn` 将根目录 `/` 反向代理到该地址。完整步骤见 `docs/BAOTA_DEPLOY.md`。

管理后台：

```text
https://fdex.k2n.cn/admin
```

后台功能：

- 管理员登录、CSRF 防护和登录限速
- 服务、主机、资源、AI 与 GitHub 状态仪表盘
- 服务地址、API 路由、CORS、端口和工作进程配置
- AI Provider、Base URL、API Key 和模型配置
- AI 接口连通性测试
- systemd 运行日志和管理员审计日志
- GitHub main/Release 检查
- 服务重启和后台更新
- 管理员密码修改

首次执行更新脚本时，会自动生成管理员密码与会话密钥，并在当前终端显示一次初始密码。API Key 仅保存在 `/opt/fdex/server/.env`，页面只显示脱敏结果。

主要地址：

```text
/                       跳转管理后台
/admin                  服务端管理后台
/docs                   Swagger API 文档
/api/info               服务信息
/api/health             健康检查
/api/version            服务版本
/api/public-config      Android 可读取的非敏感配置
```

## 服务端本地运行

```bash
cd server
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

端口配置：

```dotenv
FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2
```

端口被占用时修改 `FDEX_PORT` 并同步修改宝塔反向代理；不要结束不属于 FDEX 的其他服务。

## GitHub Actions

日常推送或 PR 会：

1. 运行 Android 单元测试
2. 编译并上传 Android Debug APK
3. 运行 FastAPI、后台登录、CSRF、页面渲染与敏感信息隔离测试

发布 Android 正式版本前，在仓库 Secrets 配置：

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEY_ALIAS
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_PASSWORD
```

然后推送语义版本标签：

```bash
git tag v1.0.1
git push origin v1.0.1
```

Release 工作流会使用固定签名构建 APK、创建 GitHub Release 并上传安装包。Android 覆盖更新必须始终使用同一签名证书。
