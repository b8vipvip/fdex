# FDEX 开发进度

最后更新：2026-08-15

## 当前正式基线

- Android 正式版：v1.1.7
- 服务端：FastAPI + systemd + 宝塔/Nginx
- Android：Kotlin + Jetpack Compose
- AI 接入：服务端多供应商管理，客户端不保存第三方 API Key
- 更新链路：GitHub Release → FDEX 服务端缓存 → Android 检查更新

## 已完成

### Android 与业务

- 消息 / 工作 / 发现 / 我的四个主入口
- 本机账号、员工、工作、群聊、资料与报告等基础业务数据
- 内置私人助理以及自定义 AI 员工
- 员工私聊和工作群流式聊天
- ChatGPT 风格正文消息与 Markdown 渲染
- 聊天“＋”附件入口：图片、视频、语音/音频、普通文件
- 图片附件接视觉理解；WAV/MP3 接语音能力路由
- App 内服务端检查更新、APK 校验与覆盖安装

### AI 服务端

- 多供应商优先级、启停、主备模型、加密 API Key
- 文本与视觉默认共用模型池，可选独立视觉覆盖模型
- 图片生成模型池与 `/images/generations` 路由
- 普通语音模型：Chat Audio / Speech(TTS) 路由
- chat2api/OpenAI-compatible SSE 文本流式透传
- reasoning/status 公开事件兼容
- 普通测试、文本深测、专项测试、自动文本深测 timer

## 2026-08-15 当前改造：图片回传修复 + Realtime 语音

### 1. 图片生成回传修复

问题：服务端 `/api/client/ai/stream` 已经会发送 `type=media`，但 Android 原客户端只解析 status/reasoning/content/done/error，导致图片媒体事件被静默丢弃。上游即使已经成功出图，聊天页仍看不到图片。

改动：

- Android `ClientAiApi` 增加 `media` SSE 事件解析。
- 非流式兼容请求同时解析响应中的 `media[]`。
- AI 图片/音频媒体使用可持久化消息标记写入聊天正文，重进会话后仍能恢复。
- 新增富媒体 AI 消息渲染器：图片直接在消息中显示并可点击查看原图；音频显示播放入口。
- 保留原 Markdown 正文，自动去掉与媒体卡重复的“查看生成图片/播放语音”链接。

验收目标：

1. 发送“生成一张……”后上游成功出图。
2. FDEX SSE 收到 `media`。
3. Android 当前流式消息立即显示图片。
4. 退出并重新进入聊天后图片仍显示。

### 2. Realtime 实时语音对话

改动：

- 新增 `WS /api/client/voice/realtime`。
- 服务端按供应商优先级选择配置了 Realtime/Live 语音模型的供应商，API Key 只保留在服务端。
- Android 通过 WebSocket 只连接 FDEX，不直接连接第三方供应商。
- Android 使用 `AudioRecord` 采集 24 kHz mono PCM16，并连续发送音频 chunk。
- 服务端转换为 OpenAI-compatible Realtime `input_audio_buffer.append` 事件。
- 支持 server VAD、实时状态、用户转写、AI 转写、PCM16 音频 delta 和 response done。
- Android 使用 `AudioTrack` 边收边播放模型语音，启用 VOICE_COMMUNICATION 音频模式并在设备支持时启用 AcousticEchoCanceler。
- 员工聊天输入栏增加独立麦克风按钮；“＋ → 语音”继续用于发送已有音频文件，两种入口职责分开。
- 实时语音对话中的 AI 文本转写会写入员工聊天记录。

供应商要求：

- 当前 Realtime 桥按 OpenAI-compatible Realtime WebSocket 协议工作。
- `audio_protocol=auto` 时，模型名包含 `realtime` 或 `live` 的语音模型会作为实时候选。
- 普通 `chat_audio` / `speech` 仍走已有单次请求链路，不伪装成实时双向通话。
- chat2api 当前仓库自身仍明确标注“语音生成、语音对话尚未实现”，因此 FDEX Realtime 会使用其它真正支持 Realtime 的供应商；不会修改 chat2api 代码或伪造其能力。

### 3. 部署要求

- 服务端新增显式 `websockets` 依赖。
- Android 新增 RECORD_AUDIO 权限。
- Android 新增 OkHttp WebSocket 与 Coil 图片显示依赖。
- 宝塔/Nginx 必须允许 `/api/client/voice/realtime` WebSocket Upgrade；如果站点根反向代理没有传递 Upgrade/Connection，需要补充 WebSocket 代理头。

## 待继续验证 / 后续

- 在真实配置的 Realtime 供应商上做 Android 真机双向语音长连接测试，包括打断、网络切换、蓝牙耳机与回声控制。
- 根据实际供应商事件格式继续增加 Realtime 协议适配器；当前基线是 OpenAI-compatible Realtime。
- chat2api 如果未来实现自身语音桥，可直接作为新的 Realtime/Audio 供应商接入 FDEX，无需改 Android 业务层。
- 视频/普通文档当前可以作为聊天附件保存与打开；是否直接交给模型理解仍按具体上游协议逐项接入，不假装已读取。

## 发布流程

1. 功能分支 CI：FastAPI Tests + Android unit tests + Debug APK。
2. 合并 main 后，用 `[release]` 提交触发正式签名构建。
3. GitHub Release 生成后，FDEX 服务端 release-sync 自动缓存 APK。
4. Android 通过 FDEX 服务端检查并下载最新版。
