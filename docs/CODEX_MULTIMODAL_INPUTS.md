# FDEX Codex Multimodal Turn Inputs / FDEX Codex 多模态 Turn 输入

> Phase 7.29 / 7.29 阶段

## 中文

FDEX 不把图片、音频、Skill 或文件引用拼进文本 Prompt。Phase 7.29 直接生成官方 Codex App Server `UserInput[]`：

- `text`：任务文本；
- `localImage`：FDEX owner/task 隔离目录中的 PNG/JPEG/WebP；
- `localAudio`：FDEX owner/task 隔离目录中的 MP3/WAV/M4A；
- `skill`：当前 FDEX 用户自己的 `CODEX_HOME/skills/<name>/SKILL.md`；
- `mention`：当前任务 Git worktree 内经过边界校验的仓库相对路径。

### 权限边界

浏览器永远不能提交一个可直接信任的服务器绝对路径。上传媒体由 FDEX 生成文件名并复制到 `server/data/codex-input-assets/<owner-hash>/<task-id>/`。Mention 只保存仓库相对路径，在 worktree 真正建立以后再 `resolve()`；绝对路径、`..` 与符号链接逃逸均拒绝。Skill 只接收安全名称，并在 Turn 启动时重新解析到该 owner 的 `CODEX_HOME/skills`，符号链接逃逸同样拒绝。

图片最大 20 MiB，音频最大 50 MiB；MIME 与常见文件签名同时检查。媒体文件使用 0600、目录尽量使用 0700。单任务最多 24 个附加输入。

### 生命周期

输入只能在 task 状态为 `queued` 时修改。一旦开始执行，输入被冻结。普通 Retry 若没有显式新输入，会复制源任务的输入；官方 Thread Resume/Fork 不重复复制旧媒体，因为历史已经属于 Thread context。

永久账号删除会同时删除 `codex_task_inputs` 元数据和 owner 媒体目录。任务执行时，Host 仍使用原有 provider、sandbox、approval、Remote MCP 与 GitHub authority 边界。

### UI

用户中心增加“Agent 输入”入口。建议流程：

1. 创建 Coding Agent 任务时取消“创建后立即执行”；
2. 打开“Agent 输入”；
3. 选择 queued 任务并添加图片、音频、Skill 或 Mention；
4. 返回任务详情并开始执行。

## English

Phase 7.29 emits the official Codex App Server `UserInput[]` union instead of encoding media, skills, or file references as prompt text.

- `text` carries the normal task prompt.
- `localImage` and `localAudio` point only to FDEX-generated owner/task assets.
- `skill` resolves only below the current owner's `CODEX_HOME/skills/<name>/SKILL.md`.
- `mention` starts as a repository-relative path and is resolved only after the isolated task worktree exists.

Browser-provided absolute server paths are never trusted. Absolute paths, parent traversal, and symlink escapes fail closed. Images are limited to 20 MiB and audio to 50 MiB with MIME plus signature checks. Inputs become immutable once execution starts, and permanent account deletion erases both metadata and owner-scoped media assets.

This phase intentionally does not implement Skill installation/update policy, Hooks/Plugins management, sub-agent governance, whole-process-tree cgroups, Runtime staged upgrade/rollback, or provider compatibility smoke tests; those remain separate control-plane phases.