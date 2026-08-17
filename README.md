# FDEX

FDEX 是 **Android 原生客户端 + FastAPI 服务端 + 中文管理后台**。旧的 `ai-business-assistant/` Web 客户端已经删除，不兼容旧版 Web 数据或接口。

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
- 启动时通过 FDEX 服务端检查当前真正的 latest Release，也可手动检查
- 发现新版本后从 FDEX 服务端缓存下载 APK，校验 SHA-256 后调用系统安装器
- 默认通过 `https://fdex.k2n.cn` 访问 FDEX 服务端
- 员工、聊天、工作、资料索引、报告和工作群等增长型业务数据保存在本机 SQLite
- 从旧版 `fdex_app_v2` SharedPreferences 首次启动时自动迁移到 SQLite，不破坏既有用户数据
- 本地账号密码使用 PBKDF2-HMAC-SHA256 + 随机盐，并由 Android Keystore 中不可导出的 HMAC 密钥二次保护；旧 SHA-256 账号在下一次成功登录后自动升级

附件分析：

- 图片直接进入视觉模型
- 视频在 Android 本机抽取最多 4 个代表画面后进入视觉模型；当前不把视频音轨当作已识别内容
- WAV / MP3 可进入语音能力路由
- PDF、DOCX、XLSX/XLSM、PPTX 和常见文本/代码/配置文件会临时发送到 FDEX 服务端，在内存中提取正文后进入 AI 上下文
- 不支持或提取失败的文件会明确标注，禁止仅凭文件名假装已经读取

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
- 多 AI Provider、优先级、Base URL、API Key、文本/视觉/图片/语音模型配置
- AI 接口连通性与深度测试
- systemd 运行日志和管理员审计日志
- Realtime 语音诊断日志下载
- GitHub main/Release 检查
- 服务重启和后台更新
- 管理员密码修改

首次执行更新脚本时，会自动生成管理员密码与会话密钥，并在当前终端显示一次初始密码。AI Provider Key 使用独立 Fernet 密钥加密保存到 `server/data/ai-providers.db`，密钥文件为 `server/data/ai-providers.key`；后台页面只显示脱敏结果。旧 `.env` AI 配置仅用于兼容迁移和服务级配置，不再是多供应商 Key 的主存储位置。

主要地址：

```text
/                       跳转管理后台
/admin                  服务端管理后台
/docs                   Swagger API 文档
/api/info               服务信息
/api/health             健康检查
/api/version            服务版本
/api/public-config      Android 可读取的非敏感配置
/api/client/ai          Android 非流式 AI 网关
/api/client/ai/stream   Android SSE AI 网关
/api/client/voice/realtime  Realtime WebSocket
/api/client/update      latest_only 客户端更新检查
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

日常推送到 `main`、`agent/**` 或创建针对 `main` 的 PR 会：

1. 运行 Android 单元测试
2. 编译并上传 Android Debug APK
3. 安装服务端依赖并运行 FastAPI 测试

### 当前正式发布主流程

正式版本优先走 `.github/workflows/auto-tag-release.yml`：

1. 将准备发布的变更合并/推送到 `main`
2. 发布提交信息必须包含 `[release]`
3. `Build and Test` 全部成功后，自动发布工作流读取当前最新 `vMAJOR.MINOR.PATCH`
4. 自动把 patch +1，并创建新 tag
5. 使用固定 Android 签名构建 Release APK
6. 自动创建 GitHub Release 并上传 APK

示例提交信息：

```text
[release] 修复附件解析与本地存储
```

发布 Android 正式版本前，仓库 Secrets 必须配置：

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEY_ALIAS
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_PASSWORD
```

Android 覆盖更新必须始终使用同一签名证书。

### 手工 tag 发布仅作为备用

`.github/workflows/release.yml` 仍支持手工推送语义版本 tag：

```bash
git tag v1.2.0
git push origin v1.2.0
```

不要对同一版本同时使用 `[release]` 自动发布和手工 tag 发布。日常正式版本默认使用 `[release]` 主流程，手工 tag 只在需要明确指定版本号时使用。

## 客户端更新策略

Android 不直接把任意旧缓存 APK 当作最新版本。`/api/client/update` 使用 `latest_only`：

1. FDEX 服务端先确认 GitHub 当前 latest stable Release
2. 如果服务端 APK 缓存落后，先触发同步并返回等待状态
3. 缓存与 GitHub latest 一致后才向 Android 返回下载地址
4. 因此跨多个版本升级时直接安装当前最新版，不逐个安装中间版本
