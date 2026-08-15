# FDEX 开发进度

最后更新：2026-08-15

## 当前正式基线

- Android 正式版：v1.1.9
- v1.1.9 状态：已发布；修复图片长耗时提前回退，并正式适配 chat2api GPT-Live 实时语音协议
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
- App 内服务端检查更新、APK 校验与覆盖安装

### AI 服务端

- 多供应商优先级、启停、主备模型、加密 API Key
- 文本与视觉默认共用模型池，可选独立视觉覆盖模型
- 图片生成模型池与 `/images/generations` 路由
- 普通语音模型：Chat Audio / Speech(TTS) 路由
- OpenAI-compatible Realtime 语音供应商 WebSocket 桥
- chat2api `chat2api-live-v1` GPT-Live WebSocket 桥
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

核对 chat2api `agent/live-voice-external-app-v20-2` 后确认：它的实时协议不是 OpenAI Realtime wire protocol，而是自定义 `chat2api-live-v1`。

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

## 部署要求

- 服务端需要 `websockets` 依赖。
- Android 需要 RECORD_AUDIO / MODIFY_AUDIO_SETTINGS 权限。
- 宝塔/Nginx 必须允许 `/api/client/voice/realtime` WebSocket Upgrade。
- 普通 `location /` 需要关闭 SSE buffering，并建议使用 600 秒 read/send timeout 以兼容长耗时媒体任务。
- chat2api GPT-Live 需要部署包含 `agent/live-voice-external-app-v20-2` 实时语音实现的版本，并使用具有 chat/audio scope 的 managed API Key。

## 待继续验证 / 后续

- 真机验证 chat2api 图片生成超过 60 秒后的完整 media 回显。
- 真机验证 chat2api GPT-Live 16k 上行 / 24k 下行双向音频、打断和文本转写。
- 验证蓝牙耳机、扬声器回声控制和网络切换。
- chat2api Live 分支未来合入 main 后，更新文档中的分支依赖说明。
- 视频/普通文档当前可以作为聊天附件保存与打开；是否直接交给模型理解仍按具体上游协议逐项接入。

## 发布流程

1. 功能分支 CI：FastAPI Tests + Android unit tests + Debug APK。
2. 合并 main 后，用 `[release]` 提交触发正式签名构建。
3. GitHub Release 生成后，FDEX 服务端 release-sync 自动缓存 APK。
4. Android 通过 FDEX 服务端检查并下载最新版。
