# FDEX 开发进度

最后更新：2026-08-25

## 2026-08-25 接续开发基线

- **v1.1.32 / Phase 7.4 已发布**：PR #62 合并后，持久化 Coding Agent 任务/事件、跨 worker 取消与重试、账号沙箱磁盘预算和安全清理已经进入正式 Release。
- **Phase 7.5 当前开发分支**：GitHub Device OAuth + 账号级仓库发现，目标是让 Android 不再收集/保存 GitHub Token。
- 每个 Device Flow、GitHub 连接、仓库项目都绑定 FDEX 中心账号 `user_id`；跨账号读取 flow/connection/repository 会失败。
- `device_code`、access token、refresh token 只在服务端 Fernet 加密保存；API 只返回用户码、GitHub 确认地址和脱敏连接元数据。
- 服务端按 GitHub 的 `interval` 强制限制轮询；expiring token 在跨 worker 文件锁内轮换并重新验证 GitHub user id。
- Android 授权完成后只展示该 GitHub 身份通过 `/user/repos` 可访问的仓库，并根据 `permissions.push` 决定是否启用 Agent push/PR。
- 管理后台新增 OAuth Client ID/scope 配置；旧 PAT Connector 仅作为迁移/应急兼容入口。

## 当前正式基线

- Android 正式版：**v1.1.32**
- v1.1.32 状态：**已发布**；Phase 7.4 持久化 Agent 任务和沙箱生命周期已上线
- 服务端：FastAPI + systemd + 宝塔/Nginx
- Android：Kotlin + Jetpack Compose
- AI 接入：服务端多供应商管理，客户端不保存第三方 API Key
- 更新链路：GitHub Release → FDEX 服务端缓存 → Android `latest_only` 检查更新
- 当前更新策略：客户端无论落后多少版本，只安装服务器确认过的最新正式 Release，不逐版升级

## 当前架构原则

### 员工 Prompt

- **FDEX 不再内置或自动拼接员工固定 Prompt。**
- 员工 Prompt 由 Android 客户端的员工资料字段 `rolePrompt` 保存、显示和编辑。
- 员工私聊、工作群、Realtime 实时语音都只使用该员工当前保存的 `rolePrompt` 原文。
- `rolePrompt` 为空时，不发送员工 system message；不会自动补一句“你是 FDEX 员工……”之类隐藏提示词。
- 服务端只接受并透传客户端传来的 `system`，不维护员工角色模板。
- 旧版本本地已经保存的员工和 Prompt 不做破坏性迁移；升级后用户可在客户端编辑并覆盖。

### AI 与密钥

- 第三方供应商 API Key 只保留在 FDEX 服务端。
- 文本、视觉、图片、普通语音、Realtime 根据服务端供应商配置路由。
- Realtime 通话中的纯文字固定进入同一个实时会话，不重新选择供应商或模型。

## 已完成

### Android 与业务

- 消息 / 工作 / 发现 / 我的四个主入口。
- 本机账号、员工、工作、群聊、资料与报告等基础业务数据。
- 自定义 AI 员工与员工资料编辑。
- 员工私聊和工作群流式聊天。
- ChatGPT 风格正文消息与 Markdown 渲染。
- 聊天“＋”附件入口：图片、视频、语音/音频、普通文件。
- 图片附件接视觉理解；WAV/MP3 接语音能力路由。
- 员工私聊实时语音双入口：输入框右侧麦克风 + `＋ → 实时语音通话`。
- 实时语音使用聊天页顶部悬浮声波条，支持麦克风、扬声器与结束控制。
- 实时语音期间可发送纯文字到同一 Realtime 会话，不重新选择供应商或模型。
- Android 系统返回键/边缘返回手势已接入 FDEX 自己的 route/history 页面栈。
- Android 12+ 实时语音使用 CommunicationDevice 路由，并申请语音通信音频焦点。
- App 内服务端检查更新、APK SHA-256 校验与覆盖安装。
- 跨版本更新只安装当前最新正式版。

### AI 服务端

- 多供应商优先级、启停、主备模型、加密 API Key。
- 文本与视觉默认共用模型池，可选独立视觉覆盖模型。
- 图片生成模型池与 `/images/generations` 路由。
- `GPT Image / gpt image / gpt_image / gpt-image` 自动规范为上游模型 ID `gpt-image`。
- 普通语音模型：Chat Audio / Speech(TTS) 路由。
- OpenAI-compatible Realtime WebSocket 桥。
- chat2api `chat2api-live-v1` GPT-Live WebSocket 桥。
- Realtime 同会话文字注入与 Barge-in 打断。
- Realtime 端到端诊断日志：上游/下游 PCM、Android 接收/播放、AudioTrack、音频路由、打断时序和会话汇总。
- 管理后台可鉴权下载实时语音诊断日志，不公开暴露 `server/data`。
- chat2api/OpenAI-compatible SSE 文本流式透传。
- reasoning/status/media 事件兼容。
- 供应商普通测试、文本深测、专项测试、自动文本深测 timer。

## v1.1.8：图片 media 回传 + 基础 Realtime

状态：**已发布。**

- Android 开始解析 `media` SSE 事件和非流式 `media[]`。
- AI 图片/音频媒体进入可持久化聊天消息。
- 新增 `WS /api/client/voice/realtime`。
- Android 只连接 FDEX，第三方 API Key 不下发手机。

## v1.1.9：图片长耗时 + chat2api GPT-Live

状态：**已发布。**

- 图片生成使用独立长等待窗口并持续发送 SSE 状态心跳，避免 60 秒文本超时提前回退。
- Android AI HTTP read timeout 放宽，Nginx 模板同步适配长耗时媒体任务。
- `GPT Live / gpt-live / gpt_live` 等名称完成规范化。
- FDEX 适配 chat2api `WS /v1/audio/realtime`：16 kHz PCM16 上行、24 kHz PCM16 下行。

## v1.1.10：聊天页内实时语音

状态：**已发布。**

- 实时语音不再弹独立 Dialog，只在聊天页顶部显示悬浮声波横条。
- 横条支持麦克风开关、扬声器/听筒切换、结束通话。
- 通话期间保留文本和附件输入区。
- 同步修复 `GPT Image` → `gpt-image` 模型别名。

## v1.1.11：同会话文字 + 完整 Barge-in

状态：**已发布。**

- Realtime active 时纯文字通过当前 `RealtimeVoiceSession` 发送，不走普通供应商池。
- 同一通话固定同一个供应商、模型和 WebSocket 会话。
- 用户重新开口时：上游取消当前回答，Android `AudioTrack` 立即 `pause → flush → play` 清空剩余播放缓存。
- 被打断的回答不会与下一轮文本缓冲串接。

## v1.1.12：系统返回手势

状态：**已发布。**

- Android Back / 左侧边缘右滑优先返回 FDEX 应用内上一页。
- 工作 / 发现 / 我的根 Tab 先回消息首页；消息根页才允许退出 App。
- 员工聊天菜单打开时返回手势优先关闭菜单。
- 增加导航策略回归测试。

## v1.1.13：实时语音无声修复 + 端到端诊断

状态：**已发布。**

- Android 12+ 改用 `setCommunicationDevice()` 选择扬声器/听筒，旧接口只做兼容回退。
- Realtime 申请语音通信音频焦点。
- AudioTrack 增加初始化、写入、播放状态和字节统计。
- 服务端新增 `/opt/fdex/server/data/realtime-voice.log` JSONL 诊断日志。
- 日志记录供应商/模型/协议、PCM 帧和字节、Android 收包/播放、音频路由、打断时序，不记录 API Key、原始音频和聊天正文。

## v1.1.14：跨版本只更新最新版

状态：**已发布。**

- 客户端更新策略固定为 `latest_only`。
- 服务端返回 APK 前确认本地缓存是否对应 GitHub 当前最新正式 Release。
- 如果 GitHub 已有更高版本而本地 APK 尚未同步完成，接口返回“等待服务器缓存”，不再把旧缓存版本交给客户端安装。
- 例如客户端从 v1.1.8 检查更新时，如果最新是 v1.1.14，则直接安装 v1.1.14，不安装 v1.1.9 → v1.1.10 → ...。

## v1.1.15：员工 Prompt 完全客户端化 + 创建员工优化

状态：**PR #32 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

### 1. 删除员工隐藏系统 Prompt

- 删除员工私聊自动拼接的固定前后缀，例如“你是 FDEX AI 虚拟公司的员工……”和“像真实同事一样……”。
- 删除工作群自动拼接的“你是工作群里的……”和团队协作固定指令。
- 员工私聊、群聊和 Realtime 统一只读取 `employee.rolePrompt`。
- Prompt 为空时传 `null`，不生成任何员工 system message。
- 服务端不增加新的员工 Prompt；现有 `/api/client/ai` 继续只做客户端 `system` 透传。

### 2. 不再自动种固定员工 Prompt

- 新账号注册和登录不再调用 `seedEmployees()`。
- 删除原先自动创建的小知 / 小策 / 小研 / 小执固定员工及其固定职责 Prompt。
- `bulkAddEmployees()` 仍可创建基础员工记录，但 Prompt 默认为空，不再自动生成岗位固定模板。
- 旧版本已经存在于 SharedPreferences 的员工资料和 Prompt 原样保留，避免升级造成用户数据丢失；用户可在客户端逐个修改。

### 3. 一句话 AI 生成员工提示词

创建/编辑员工页面新增：

- “一句话描述你想要的员工”输入框。
- “根据一句话 AI 生成提示词”按钮。
- 客户端根据员工名称、部门、职位和用户一句话组织一次普通 AI 请求。
- 生成请求使用 `system = null`，避免再引入一层隐藏员工 system prompt。
- AI 结果直接写入“员工提示词（客户端保存）”编辑框。
- 用户可在保存前继续自由修改生成结果。
- 保存后该 Prompt 作为员工长期资料持久化，后续聊天直接使用。

### 4. 员工名称 / 部门 / 职位随机与手动编辑

- 新建员工页面进入时，**部门和职位自动随机预填**。
- 员工名称输入框后增加“随机”按钮。
- 部门输入框后增加“随机”按钮。
- 职位输入框后增加“随机”按钮。
- 三个字段仍是普通可编辑文本框，用户可随时手动覆盖随机结果。
- 本地提供多组常用员工名称、部门和岗位候选，每次点击独立随机，不要求三项绑定。

### 5. 已有员工编辑

- 新增 `EditEmployee` 页面路由。
- 员工管理列表增加“编辑”。
- 员工聊天右上角菜单增加“编辑员工”。
- 已有员工可修改名称、部门、职位和 Prompt。
- 员工管理列表显示“Prompt 已由客户端保存 / Prompt 未设置”，方便识别旧数据是否需要补配置。

### 6. 验收项目

1. 新注册账号不自动出现带固定 Prompt 的员工。
2. 创建员工时部门和职位自动出现随机值。
3. 名称、部门、职位三个输入框后均可独立点击“随机”。
4. 随机结果可以手动修改。
5. 输入一句员工描述后可调用现有 AI 线路生成完整 Prompt。
6. 生成后的 Prompt 可以再次手工编辑再保存。
7. 员工私聊请求中的 system 内容与客户端保存的 `rolePrompt` 完全一致，无额外固定前缀/后缀。
8. 群聊和 Realtime 使用同一份 `rolePrompt`。
9. 老用户升级后既有员工资料不丢失，并可进入编辑页修改 Prompt。

### v1.1.15 同步：后台实时语音诊断下载

PR #31 已合并：

- `/admin/maintenance` 增加“实时语音诊断”区域。
- 已登录管理员可下载 `fdex-realtime-voice.log`。
- 日志未生成时明确提示先完成一次实时语音通话。
- `server/data` 不作为公开静态目录暴露。

## 部署要求

- 服务端需要 `websockets` 依赖。
- Android 需要 `RECORD_AUDIO` / `MODIFY_AUDIO_SETTINGS` 权限。
- 宝塔/Nginx 必须允许 `/api/client/voice/realtime` WebSocket Upgrade。
- 普通 `location /` 关闭 SSE buffering，并建议 600 秒 read/send timeout 兼容长耗时图片任务。
- Realtime 诊断日志默认位于 `/opt/fdex/server/data/realtime-voice.log`，仅管理员读取/下载。
- GitHub Release 同步由 `fdex-release-sync.timer` 定时执行。

## 待继续验证 / 后续

- 真机验证 v1.1.15 员工创建页的随机按钮、AI Prompt 生成与员工编辑流程。
- 抓取一轮员工聊天请求，确认 system 与客户端保存 `rolePrompt` 字节级一致且无额外隐藏前后缀。
- 真机继续验证 Realtime 扬声器播放与 Barge-in，根据后台可下载的实时语音诊断日志定位剩余设备兼容问题。
- chat2api 完成 Live `input.text` 后，验证通话中纯文字保持同一 GPT-Live 会话。
- 真机继续验证图片生成完整 media 回显。
- 视频/普通文档当前可作为聊天附件保存与打开；后续按具体上游能力接入内容理解。

## 发布流程

1. 功能分支 CI：FastAPI Tests + Android unit tests + Debug APK。
2. 合并 main 后，使用 `[release]` 提交触发正式签名构建。
3. GitHub Release 生成后，FDEX 服务端 release-sync 自动缓存最新 APK。
4. Android 更新接口采用 `latest_only`，只下载当前最新正式版。
