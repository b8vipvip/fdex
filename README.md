# FDEX

FDEX 已重构为 **Android 原生客户端 + FastAPI 服务端**。旧的 `ai-business-assistant/` 目录和 Web 前端不再保留，也不兼容旧版数据或接口。

## 目录结构

```text
.
├── app/                    # Android 客户端（Kotlin + Jetpack Compose）
├── server/                 # FastAPI 服务端
├── deploy/                 # systemd 与宝塔配置
├── scripts/                # 服务端更新脚本
├── .github/workflows/      # GitHub Actions 构建与发布
├── build.gradle.kts
├── settings.gradle.kts
└── gradle.properties
```

## Android 客户端

### 功能

- 原生 Android App，最低支持 Android 8.0（API 26）
- “设置 → 关于”显示版本名、版本号和构建提交
- “检查更新”按钮主动检查 GitHub Release
- App 启动时每 6 小时自动检查一次更新
- 发现新版本时弹出更新提示
- 可从 GitHub Release 下载 APK 并调用系统安装器更新
- 默认通过 `https://fdex.k2n.cn` 访问服务端

### 本地构建

本仓库不提交 Gradle Wrapper 二进制文件。安装 JDK 17 和 Gradle 8.9 后执行：

```bash
gradle :app:assembleDebug
```

APK 输出：

```text
app/build/outputs/apk/debug/app-debug.apk
```

## 服务端

```bash
cd server
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

默认监听：

```text
127.0.0.1:18080
```

服务端端口由 `server/.env` 配置：

```dotenv
FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2
```

端口被占用时，修改 `FDEX_PORT` 并同步修改宝塔反向代理，不要结束其他服务。完整部署步骤见 `docs/BAOTA_DEPLOY.md`。

接口：

- `GET /api/health`
- `GET /api/version`
- `GET /api/public-config`

## GitHub Actions

### 日常构建

推送或创建 PR 后，`.github/workflows/ci.yml` 会：

1. 编译 Android Debug APK
2. 上传 APK 构建产物
3. 运行 FastAPI 服务端测试

### 发布新版本

1. 在仓库 Secrets 中配置：

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEY_ALIAS
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_PASSWORD
```

2. 创建并推送版本标签：

```bash
git tag v1.0.1
git push origin v1.0.1
```

3. `.github/workflows/release.yml` 会自动：

- 根据标签生成 `versionName`
- 根据语义版本生成递增 `versionCode`
- 使用固定签名证书构建 Release APK
- 创建 GitHub Release
- 上传 `fdex-1.0.1.apk`

> Android 只能用同一签名证书覆盖更新。首次发布后必须妥善保存签名文件和密码，不能更换。

## 更新机制

客户端调用：

```text
https://api.github.com/repos/b8vipvip/fdex/releases/latest
```

Release 标签须使用 `v主版本.次版本.修订版本`，例如 `v1.2.3`。Release 中必须包含 `.apk` 文件。

Android 8.0 及以上首次通过 App 更新时，系统会要求允许“安装未知应用”。这是 Android 系统安全限制，App 无法绕过。
