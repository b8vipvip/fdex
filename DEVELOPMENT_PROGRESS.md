# FDEX 开发进度

最后更新：2026-08-15

## 当前正式基线

- Android 正式版：v1.1.13
- v1.1.13 状态：已发布；修复实时语音客户端播放路由，并增加端到端语音诊断日志
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
- 员工私聊实时语音双入口：输入框右侧麦克风 + `＋ → 实时语音通话`
- 实时语音使用聊天页顶部悬浮声波条，支持麦克风、扬声器与结束控制
- 实时语音期间可发送纯文字到同一 Realtime 会话，不重新选择供应商或模型
- Android 系统返回键/边缘返回手势已接入 FDEX 自己的 route/history 页面栈
- Android 12+ 实时语音使用正式 CommunicationDevice 路由，并申请语音通信音频焦点
- App 内服务端检查更新、APK 校验与覆盖安装

### AI 服务端

- 多供应商优先级、启停、主备模型、加密 API Key
- 文本与视觉默认共用模型池，可选独立视觉覆盖模型
- 图片生成模型池与 `/images/generations` 路由
- `GPT Image / gpt image / gpt_image / gpt-image` 自动规范为上游模型 ID `gpt-image`
- 普通语音模型：Chat Audio / Speech(TTS) 路由
- OpenAI-compatible Realtime 语音供应商 WebSocket 桥
- chat2api `chat2api-live-v1` GPT-Live WebSocket 桥
- Realtime 同会话文字注入与 Barge-in 打断
- Realtime 端到端诊断日志：上游/下游 PCM 帧字节、Android 接收/播放、AudioTrack 状态、打断时序和会话汇总
- chat2api/OpenAI-compatible SSE 文本流式透传
- reasoning/status/media 事件兼容
- 普通测试、文本深测、专项测试、自动文本深测 timer

## v1.1.8：图片 media 回传 + 基础 Realtime 语音

状态：**已合并、CI 通过、正式签名 Release 已发布。**

### 图片生成回传

- Android `ClientAiApi` 已解析 `media` SSE 事件。
- 非流式兼容请求已解析响应中的 `media[]`。
- AI 图片/音频媒体写入可持久化消息标记，重进会话后仍能恢复。
- 富媒体 AI 消息可直接显示图片、查看原图和展示音频入口。

### 基础 Realtime

- 新增 `WS /api/client/voice/realtime`。
- Android 使用 WebSocket 只连接 FDEX，第三方 API Key 不下发手机。
- Android 基础实现为 24 kHz PCM16 输入/输出。
- 服务端第一版按 OpenAI-compatible Realtime 协议桥接。

## v1.1.9：图片长耗时修复 + chat2api GPT-Live

状态：**PR #24 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

### 1. 图片生成仍回退文本的根因与修复

真机现象：chat2api 浏览器页面最终已经成功生成图片，但 FDEX 先显示“专项模型不可用，已回退文本模型回答”，随后 Android 还可能出现“流式连接不可用，正在兼容重试”。

根因：

- 图片生成此前直接复用供应商普通文本 `timeout_seconds`，实际配置常为 60 秒；浏览器图片生成可能明显超过 60 秒，导致 FDEX 在 chat2api 尚未完成前先判失败。
- Android 普通 AI HTTP 读取窗口此前为 120 秒，长耗时媒体请求仍可能被客户端提前中断。
- 图片专项调用等待期间没有持续 SSE 数据，容易同时触发 Android / Nginx 的空闲读取超时。

修复：

- 图片生成独立使用至少 360 秒上游等待窗口，不修改供应商数据库里的文本超时值。
- 图片生成期间每 12 秒发送 SSE 状态心跳，例如“图片仍在生成，请稍候…”。
- Android AI HTTP read timeout 延长到 420 秒，作为非流式兼容请求兜底。
- 宝塔/Nginx 普通 SSE 代理模板 send/read timeout 提升到 600 秒。
- 图片专项失败时返回经过脱敏的实际失败摘要，不再只显示无信息量的“专项模型不可用”。
- 原有 `media` SSE → Android 富媒体显示逻辑继续保留。

验收目标：

1. chat2api 图片生成超过 60 秒时 FDEX 不提前回退。
2. 等待期间 Android 持续看到“图片仍在生成”状态。
3. chat2api 返回图片后 FDEX 收到 `media` 并立即显示。
4. 退出并重新进入聊天后图片仍显示。

### 2. chat2api GPT-Live 实际协议适配

核对 chat2api 对应 Live 分支后确认：它的实时协议不是 OpenAI Realtime wire protocol，而是自定义 `chat2api-live-v1`。

实际协议：

- 上游入口：`WS /v1/audio/realtime`
- 模型：`gpt-live` / `gpt-live-mini`
- 鉴权：managed API Key 通过 `Authorization: Bearer ...`
- 首帧：`session.start`
- 上行音频：16 kHz mono PCM16 little-endian **binary WebSocket frame**
- 下行音频：24 kHz mono PCM16 little-endian **binary WebSocket frame**
- 文本事件包括 `session.ready`、`transcript.final`、`response.text.delta`、`response.done`、`response.interrupted`、`error` 等。

FDEX v1.1.9：

- `GPT Live`、`gpt-live`、`gpt_live`、`gpt live mini` 等写法统一规范化识别。
- `gpt-live` / `gpt-live-mini` 自动选择 `chat2api-live-v1`；名称含 `realtime` 的模型继续走 OpenAI-compatible Realtime。
- FDEX → chat2api 改走 `/v1/audio/realtime`，不再错误请求 `/v1/realtime?model=...`。
- FDEX 服务端把 Android 内部 JSON/base64 音频解码后，以 binary frame 转发给 chat2api。
- chat2api binary 24 kHz 音频由 FDEX 转成内部音频事件回传 Android。
- `session.start`、`session.finish`、`response.cancel`、用户转写、AI 文本增量和打断事件完成协议映射。
- Android Realtime 根据服务端 `ready` 动态使用输入/输出采样率：chat2api 为 16k 输入 / 24k 输出，OpenAI-compatible 为 24k / 24k。

### 3. 实时语音入口可见性

v1.1.9 提供两个员工私聊实时语音入口：

- 输入框右侧：独立麦克风按钮。
- `＋` 菜单顶部：`实时语音通话`。
- `＋ → 语音` 仍仅用于选择已有音频文件，与实时通话职责分开。

## v1.1.10：聊天页内实时语音交互

状态：**PR #26 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

目标：实时语音不再弹出独立通话 Dialog，保持员工聊天页完整可操作。

已实现：

- 员工聊天页顶部增加悬浮声波横条，不离开当前聊天页面。
- 声波条显示当前员工、连接/听取/回答状态和实际供应商/语音模型。
- 麦克风开关：关闭后保留 WebSocket 会话，但停止向上游发送麦克风 PCM 数据；再次开启立即恢复。
- 扬声器开关：通话期间可在扬声器和系统通信听筒路由之间切换；结束通话后恢复进入通话前的系统音频路由。
- 结束通话按钮集中在悬浮声波条右侧。
- 原有文本输入框、附件菜单和发送按钮在实时语音期间保持可用；v1.1.10 的文字仍走普通文本/多模态流式链路，实时语音 WebSocket 保持连接。
- 语音最终转写继续写入同一员工聊天记录，AI 语音回复的文本转写也写入同一消息时间线。
- 实时语音进行中隐藏输入框旁重复的“开始语音”按钮，`＋` 菜单显示“实时语音进行中”，避免重复建立第二条会话。

验收目标：

1. 点击员工聊天麦克风后不再弹出 Dialog，只出现顶部悬浮声波条。
2. 麦克风关闭时服务端连接仍在线，但不再上传本地音频；开启后恢复。
3. 扬声器开/关可以在扬声器和听筒/系统通信输出路由间切换。
4. 实时语音保持连接期间，可以正常输入并发送文本、图片和其它附件。
5. 结束语音后悬浮条消失，系统音频模式和扬声器路由恢复。

### v1.1.10 同步修复：GPT Image 模型别名

- FDEX 后台可以继续显示和保存 `GPT Image` 等人类可读写法。
- 实际请求 chat2api `/v1/images/generations` 时自动规范成精确模型 ID `gpt-image`，修复 HTTP 422。
- 不改写其它供应商自己的图片模型 ID。

## v1.1.11：实时会话文字锁定 + 完整 Barge-in 打断

状态：**PR #27 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

### 1. 通话中文字不再重新选择供应商或模型

v1.1.10 中，虽然语音 WebSocket 会保持连接，但用户在通话期间输入纯文字时仍会调用普通 `/api/client/ai/stream`，因此会重新经过供应商和模型路由。v1.1.11 改为：

- 实时语音 active 且消息为纯文字时，不再调用普通 AI HTTP/SSE 接口。
- Android 通过当前 `RealtimeVoiceSession.sendText()` 向已经建立的同一个 FDEX Realtime WebSocket 发送 `{type:"text"}`。
- FDEX 服务端不重新访问供应商池；直接使用该 WebSocket 建立时已经选定的 `chosen_provider / chosen_model / chosen_protocol`。
- chat2api GPT-Live 固定转发同会话文字控制帧。
- OpenAI-compatible Realtime 固定映射成 `conversation.item.create(input_text)` + `response.create`。
- 如果实时会话尚未 ready，Android 明确提示“文字未发送；不会切换供应商或模型”，不做普通线路 fallback。
- 带图片/文件等附件的消息仍属于多模态任务，不伪装成实时纯文字；本次锁定规则针对“通话中发送纯文字”。

### 2. Barge-in 打断现状与修复

检查确认 v1.1.10 之前只具备部分打断能力：

- OpenAI Realtime session 已配置 `server_vad` + `interrupt_response=true`。
- chat2api Live 协议已经存在 `response.cancel` 与 `response.interrupted`。
- 但 FDEX 没有在 chat2api 返回 `input_audio_buffer.speech_started` 时主动发送 `response.cancel`。
- Android 收到打断状态后也没有主动清理 `AudioTrack` 中已经排队的 AI PCM，因此即使上游停止，手机仍可能继续播放一小段缓存音频。
- 被打断的 `currentReply` 也存在与下一轮回答继续拼接的风险。

v1.1.11 补全为：

1. 用户重新开口，chat2api 报 `input_audio_buffer.speech_started`。
2. FDEX 向同一 chat2api Live WebSocket 发送 `response.cancel`。
3. chat2api 浏览器桥停止当前回答，并返回 `response.interrupted`。
4. FDEX 将 `speech_started` 与 `response.interrupted` 都归一化成内部 `interrupt` 事件。
5. Android 一收到 `interrupt`，立即对当前 `AudioTrack` 执行 `pause → flush → play`，清除尚未播放的 AI 音频。
6. 已经实际说出的 AI 文本转写单独保存，然后清空当前 response buffer，下一轮回答从空缓冲开始。
7. OpenAI-compatible Realtime 继续依赖服务端 VAD 的 `interrupt_response=true`，同时同样把 `speech_started` 作为本地音频 flush 信号。

### 3. 回归测试

- FastAPI 测试覆盖 Realtime 同会话文字契约。
- FastAPI 测试覆盖 OpenAI Realtime `conversation.item.create + response.create` 文本映射。
- 测试 `speech_started` 会归一化为 `interrupt`。
- 测试 chat2api `response.interrupted` 会归一化为 `interrupt`。
- Android 构建验证新增 `Interrupted` 事件、`sendText()` 和 AudioTrack flush 逻辑可以正常编译。

## v1.1.12：系统返回手势接入应用页面栈

状态：**PR #28 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

- 修复手机左侧向右滑/系统 Back 在二级页面直接结束 `MainActivity`、回到桌面的问题。
- Android `BackHandler` 统一接入 FDEX 的 `route + history`。
- 员工聊天、群聊、工作详情、设置、账号、关于等页面优先回应用内上一页。
- 工作 / 发现 / 我的根 Tab 无内部历史时先回消息页；消息根页才允许退出 App。
- 注册页返回登录；员工聊天右上角菜单展开时先关闭菜单。
- 增加返回策略单元测试，避免后续导航改动重新引入同类问题。

## v1.1.13：实时语音无声修复 + 端到端诊断

状态：**PR #29 已合并，FastAPI / Android 全量 CI 通过，正式签名 Release 已发布。**

### 1. Android 播放链路修复

- 代码排查发现旧实现主要依赖已弃用的 `AudioManager.isSpeakerphoneOn`；Android 12+ 改用 `setCommunicationDevice()` 选择内置扬声器/听筒，旧接口仅保留兼容回退。
- 实时通话申请 `USAGE_VOICE_COMMUNICATION` 音频焦点，结束通话后恢复此前通信设备和 AudioMode。
- AudioTrack 增加初始化状态校验、显式音量、播放启动校验和更大的流式缓冲。
- 每个下行 PCM chunk 检查 AudioTrack `write()` 返回值；异常或负返回会形成诊断事件。
- 客户端累计麦克风上行、WebSocket 下行音频、AudioTrack 实际写入的帧数和字节数。

### 2. 服务端实时语音诊断日志

日志文件：`/opt/fdex/server/data/realtime-voice.log`。

- JSONL 格式，每次实时语音使用独立 `fdexrt_*` session_id。
- 文件权限固定为 0600。
- 记录供应商/模型/协议、上游连接、客户端麦克风上行、上游返回 PCM、FDEX 下发 PCM、Android 收到音频、AudioTrack 播放进度、音频路由、音频焦点、打断和最终会话 summary。
- 不记录 API Key、Authorization、原始 PCM/Base64、用户转写、聊天正文。
- `response.audio.started → speech_started → response.cancel` 会记录时间差，用来判断是否存在扬声器回声造成的误 Barge-in。
- chat2api `speech_started` 仅在当前确实有活动回答时发送 `response.cancel`，避免空闲状态无意义取消。

### 3. 诊断判读

一次真机通话结束后重点看同一个 session_id：

- `upstream_audio_bytes=0`：chat2api/浏览器桥没有把 AI PCM 返回 FDEX。
- upstream 有音频、FDEX `client_downlink_bytes>0`，但没有 `android_downlink_audio_received`：FDEX → Android WebSocket 下行/客户端版本问题。
- 有 `android_downlink_audio_received`，但没有 `android_playback_progress` 或出现 write 错误：AudioTrack/设备播放问题。
- `android_playback_progress` 正常但仍听不到：优先检查 `android_audio_route`、系统通话音量和设备路由。
- AI 刚开始出声便出现 `speech_started` + `barge_in_cancel_sent` + `android_playback_flushed_for_interrupt`：高度怀疑扬声器回声被 VAD 当成用户打断。

## 部署要求

- 服务端需要 `websockets` 依赖。
- Android 需要 RECORD_AUDIO / MODIFY_AUDIO_SETTINGS 权限。
- 宝塔/Nginx 必须允许 `/api/client/voice/realtime` WebSocket Upgrade。
- 普通 `location /` 需要关闭 SSE buffering，并建议使用 600 秒 read/send timeout 以兼容长耗时媒体任务。
- chat2api GPT-Live 需要部署包含对应实时语音实现的版本，并使用具有 chat/audio scope 的 managed API Key。
- Realtime 诊断日志默认位于 `/opt/fdex/server/data/realtime-voice.log`，只供服务器管理员读取。

## 待继续验证 / 后续

- 真机复测 v1.1.13 实时语音扬声器播放，并根据 `realtime-voice.log` 精确确认剩余故障层级。
- 真机重点验证 Barge-in：AI 说话过程中用户开口后，手机本地音频应立即停止且下一轮响应正常；同时确认扬声器回声不会误触发。
- 真机验证通话中纯文字始终留在同一供应商 / 同一模型 / 同一 GPT-Live 会话。
- 验证蓝牙耳机、扬声器回声控制和网络切换。
- 真机验证 chat2api 图片生成完整 media 回显。
- 视频/普通文档当前可以作为聊天附件保存与打开；是否直接交给模型理解仍按具体上游协议逐项接入。

## 发布流程

1. 功能分支 CI：FastAPI Tests + Android unit tests + Debug APK。
2. 合并 main 后，用 `[release]` 提交触发正式签名构建。
3. GitHub Release 生成后，FDEX 服务端 release-sync 自动缓存 APK。
4. Android 通过 FDEX 服务端检查并下载最新版。
