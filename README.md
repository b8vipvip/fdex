# FDEX

FDEX 是一个以 **FDEX Center 中心账号** 为身份边界的 AI 工作平台，当前由以下几部分组成：

- Android 原生客户端（Kotlin + Jetpack Compose）
- FastAPI 中心服务端与中文管理后台
- Web 用户中心
- 多供应商 AI 路由
- 通用「智体」聊天 / 工作群 / 知识 / 工作协作
- GitHub App Installation + Coding Agent
- 远程长期记忆与账号数据生命周期控制

当前仓库默认分支：`main`。

当前代码基线：**Phase 7.14**。

当前 Android 正式 Release：**v1.1.36**。Phase 7.14 为服务端 / Web 同步改动，没有新增 Android 运行逻辑，因此自动 Android Release 按设计跳过；v1.1.36 已包含 Phase 7.13 的 Android「智体」模型改造。

最新开发状态见 [`DEVELOPMENT_PROGRESS.md`](DEVELOPMENT_PROGRESS.md)。

## 目录结构

```text
.
├── app/                    # Android 客户端
├── server/                 # FastAPI、Web 用户中心、管理后台、AI / Agent API
├── deploy/                 # systemd 与宝塔部署配置
├── scripts/                # 服务端更新与维护脚本
├── docs/                   # 部署、架构与阶段说明
└── .github/workflows/      # CI、自动 Android Release
```

## 当前产品模型：智体

FDEX 用户侧已经从旧的「公司 / 行业 / 部门 / 岗位 / AI 员工」模型迁移为通用 **智体** 模型。

- 智体可以是老师、学习伙伴、生活助手、创作伙伴、Coding Agent 或任何用户定义身份。
- 创建智体时身份定义提示词可以留空。
- 智体名称、身份定义、知识权限、聊天权限与 Coding Agent 能力可独立配置。
- 历史数据库中的 `employee`、`employee_id`、旧 URL 等仍可作为内部兼容标识存在，但不再作为用户产品概念。
- 旧公司 / 行业 / 部门 / 岗位字段不会再作为新的产品配置写回。

## FDEX Center 账号与隔离

FDEX Center 的 `user_id` 是用户资源的唯一 owner scope。

中心账号体系已经支持：

- 注册 / 登录
- Access Token + 轮换 Refresh Token
- Android 自动续期
- 修改密码
- 忘记密码 / 邮箱验证码重置
- 用户自己的设备 / Session 管理
- 登录失败限流
- 异常登录 / 安全审计
- 数据导出
- 远程长期记忆清理
- 永久账号注销
- 旧 Android 本机数据向当前中心账号的受控迁移

GitHub、Coding Agent、Web workspace、远程记忆、任务历史和沙箱均按中心 `user_id` 隔离。

## Android 客户端

- 最低支持 Android 8.0（API 26）
- Kotlin + Jetpack Compose
- 中心账号登录 / 注册 / 找回密码
- 智体管理与智体聊天
- 工作群协作
- 工作项目与自动协作
- 知识库
- Coding Agent / GitHub 入口
- 图片、视频代表帧、音频、PDF、DOCX、XLSX/XLSM、PPTX、文本 / 代码 / 配置附件理解
- Markdown 富文本
- 单条消息复制 / 引用 / 软删除 / 恢复
- 长按正文自由文本选择
- Realtime 实时语音
- GitHub Release → FDEX 服务端缓存 → Android `latest_only` 更新

Android 本机仍保存需要离线 / 客户端运行的用户业务数据；中心账号、GitHub / Agent 权限和中心侧资源由服务端负责隔离与生命周期管理。

本地构建：

```bash
gradle :app:assembleDebug
```

Debug APK：

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Web 用户中心

Web 用户中心已经是第一方用户入口，不再是“已删除的旧 Web 客户端”。

主要能力包括：

- 消息 / 智体聊天
- 智体管理
- 知识库
- 工作与工作详情
- 工作群
- Coding Agent
- GitHub
- 发现
- 我的账号 / 安全 / 数据控制
- 最近删除与恢复

Phase 7.14 已将服务端与 Web 用户端同步到通用「智体」模型，并删除依靠浏览器 DOM 临时替换旧“员工 / 企业 / 岗位”文案的兼容做法。

## AI 服务端

FDEX 客户端不保存第三方 AI Provider API Key。

服务端统一管理：

- 多 Provider 启停与优先级
- 主 / 备模型
- Base URL / API Key 加密存储
- Chat Completions / Responses / legacy 协议顺序
- 文本
- 视觉
- 图片生成
- 普通语音 / TTS
- Realtime WebSocket
- chat2api GPT-Live 兼容
- SSE 流式转发
- reasoning / status / media 事件
- Provider 连通性与深度测试

Web 智体聊天和 Android AI 请求共用中心 Provider 池，不为 Coding Agent 再维护一套独立 AI Key / 模型配置。

## GitHub App 与 Coding Agent

当前首选 GitHub 集成是 **GitHub App Installation**，不是让 Android 用户粘贴 PAT，也不是把旧 GitHub Device OAuth 作为主流程。

用户流程：

1. FDEX 管理员初始化平台 GitHub App。
2. 用户登录 FDEX Web 用户中心。
3. 用户在 GitHub 官方页面安装 / 连接 FDEX GitHub App。
4. GitHub 决定安装到哪个账号 / 组织，以及授权全部仓库还是指定仓库。
5. FDEX 验证安装属于当前 FDEX `user_id`，并同步实际授权仓库。
6. Coding Agent 只对该 Installation 授权且当前账号可见的仓库工作。

安全边界：

- GitHub App Installation 是仓库范围和 GitHub 权限的权威来源。
- Installation Token 按操作临时签发，不返回 Android / 浏览器。
- Token 进一步限制到当前仓库和当前操作。
- 用户 A 的 FDEX `user_id` 永远不能读取用户 B 的 GitHub 连接、项目、任务、沙箱或仓库。
- 仓库被从 GitHub App Installation 移除后，会停止接受新的 Coding Agent 工作，但历史任务 ID 可保留。
- Agent 不允许直接写 `main`；工作使用独立 worktree / 分支，并可按权限 Push 分支和创建 PR。
- 任务、事件、取消、重试、沙箱磁盘使用与清理均持久化并按 owner scope 管理。
- Build / test 在受限 systemd transient unit 中执行，资源和网络策略由账号级运行策略控制。

兼容代码中仍可能保留早期 OAuth / Device OAuth / project 字段迁移路径，但它们不是当前推荐用户流程。

## 远程记忆与账号数据控制

中心账号生命周期已经覆盖远程记忆：

- MemPalace / Qdrant 记忆按服务端绑定后的账号 scope 隔离。
- Letta / Qdrant / SQLite 远程记忆支持账号级清理。
- 删除失败采用 fail-closed 策略，不会先删账号再遗留无法确认归属的远程数据。
- 永久账号删除会串行处理远程记忆、GitHub / Coding Agent 资源、沙箱、中心身份与本机账号数据。

## 服务端与管理后台

默认监听：

```text
127.0.0.1:18080
```

生产站点可通过 Nginx / 宝塔反向代理到该地址。

服务器更新：

```bash
cd /opt/fdex
sudo bash scripts/update_server.sh
```

管理后台默认入口：

```text
/admin
```

用户中心默认入口：

```text
/account
```

后台主要能力包括：

- 管理员登录、CSRF 与限流
- 服务 / 主机 / AI / GitHub 状态
- Provider 管理与测试
- FDEX Center 用户管理
- Session / 安全审计
- GitHub App 初始化
- GitHub 网络出口诊断、超时与可信代理配置
- SMTP / IMAP 配置与密码重置邮件测试
- Coding Agent 运行开关与运行策略
- Realtime 语音诊断
- systemd 日志
- GitHub main / Release 检查
- 服务端在线更新

## 主要地址

```text
/                         服务入口
/account                  Web 用户中心
/account/login            用户登录
/account/register         用户注册
/account/github           GitHub App 用户连接页
/admin                    管理后台
/docs                     Swagger API
/api/info                 服务信息
/api/health               健康检查
/api/version              服务版本
/api/public-config        Android 非敏感配置
/api/client/ai            Android 非流式 AI 网关
/api/client/ai/stream     Android SSE AI 网关
/api/client/voice/realtime Realtime WebSocket
/api/client/update        latest_only Android 更新检查
/api/auth/*               FDEX Center 账号 / Session / 数据控制
/api/agent/*              Coding Agent API
```

具体路由以 FastAPI `/docs` 和当前代码为准。

## 服务端本地运行

```bash
cd server
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.run
```

常用服务配置：

```dotenv
FDEX_HOST=127.0.0.1
FDEX_PORT=18080
FDEX_WORKERS=2
```

端口被占用时修改 `FDEX_PORT` 并同步调整反向代理，不要结束不属于 FDEX 的其他服务。

## GitHub Actions / CI

向 `main`、`agent/**` 推送或创建针对 `main` 的 PR 时，CI 会验证至少：

1. Android unit tests
2. Android Debug APK 构建
3. FastAPI Tests

当前 Phase 7.14 的 PR CI 与合并到 `main` 后的 CI 均已通过。

## Android 正式发布

正式 Android Release 优先使用 `.github/workflows/auto-tag-release.yml`。

发布条件：

1. 变更进入 `main`
2. 发布提交信息包含 `[release]`
3. `Build and Test` 成功
4. 自动将当前最新 `vMAJOR.MINOR.PATCH` 的 patch +1
5. 使用固定 Android 签名构建 Release APK
6. 创建 GitHub Release 并上传 APK

示例：

```text
[release] Phase 7.13: generalize employees into 智体
```

没有 Android 发布必要的服务端 / Web-only 提交可以不包含 `[release]`，此时 Auto Release 按设计跳过。

Android 覆盖更新必须始终使用同一签名证书。仓库 Secrets 需要正确配置：

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEY_ALIAS
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_PASSWORD
```

手工语义版本 tag 发布仅作为备用，不要对同一版本同时使用 `[release]` 自动发布和手工 tag。

## 客户端更新策略

`/api/client/update` 使用 `latest_only`：

1. FDEX 服务端确认 GitHub 当前 latest stable Release。
2. 如果服务端 APK 缓存落后，先触发同步并返回等待状态。
3. 缓存与 GitHub latest 一致后才向 Android 返回下载地址。
4. 客户端跨多个版本升级时直接安装当前最新版，不逐个安装中间版本。

## 当前兼容边界

为了避免破坏历史用户数据，代码中仍保留部分旧字段、旧 kind、旧 URL 或数据库列。这些属于迁移 / API 兼容层，不代表产品仍采用旧的“公司式 AI 员工”模型。

开发新功能时应优先使用：

- `user_id` 作为中心账号 owner scope
- 「智体」作为用户可见主体
- GitHub App Installation 作为仓库权限来源
- 中心 Provider 池作为所有 AI / Coding Agent 推理来源
- owner-scoped task / sandbox / memory 数据边界
