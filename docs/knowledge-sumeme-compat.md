# FDEX 知识库与 sumeme MemPalace 兼容设计

## 目标

FDEX 的企业知识库参考 `b8vipvip/sumeme` 的长期记忆架构，但不直接把 Python、Qdrant、Letta 运行时嵌入 Android。FDEX 继续保持本地优先：原始聊天和知识条目留在手机 SQLite，AI 只负责可选的分类、摘要和关键词整理。

这样做的原因是：sumeme 的 MemPalace 本身已经把“原文存储”和“检索索引”分开，FDEX 复用的是这套边界、作用域和去重语义，而不是强行复制服务部署形态。

## 兼容字段

FDEX `KnowledgeEntry` 使用以下与 sumeme/MemPalace 对齐的字段：

- `scope_key`：账户作用域。FDEX 使用当前账户邮箱 SHA-256 的前 24 位生成稳定、非明文 scope。
- `wing`：一级知识域。当前 FDEX 固定为 `company`。
- `room`：二级分类，例如 `technical`、`project`、`decision`、`casual`。
- `conversation_id`：来源会话，例如 `employee:123`、`group:456`。
- `source`：来源类型和 ID，例如 `employee_chat:123`、`group_chat:456`、`manual`。
- `content_hash`：原始交换内容和 scope 的 SHA-256，用于幂等去重。
- `raw_text`：本地原始知识抽屉。
- `summary` / `keywords`：派生检索内容。

未来如果增加 FDEX ↔ sumeme 同步，这些字段可以直接映射到 MemPalace drawer metadata；无需重新解释会话来源和账户边界。

## 自动沉淀

每个完成的员工私聊、工作群对话和可配对的实时语音交换都会：

1. 立即写入本地知识库，避免后台 AI 整理失败导致原始记录丢失。
2. 先使用本地规则生成保底分类、摘要和关键词，知识立刻可检索。
3. 后台调用 FDEX AI 网关做精细分类、主题摘要和关键词优化。
4. 纯问候、谢谢、哈哈、好的等无业务信息短对话额外归类为 `casual / 日常闲聊`。
5. 升级后的历史聊天通过幂等 backfill 导入，不重复生成同一条知识。

## 员工 ACL

员工权限独立存储为 `employee_permission` 记录，不写入员工 Prompt，也不修改员工角色设定。

当前权限：

- 读取知识库：允许检索 `shared_for_agents=true` 的知识条目。
- 写入知识库：该员工后续聊天生成的知识摘要可成为共享知识；关闭时聊天仍归档，但不会因为系统归档而自动赋予其他员工读取权。
- 聊天记录范围：`none / self / all / selected`。
- `selected` 保存允许读取的员工 ID 列表。

默认权限遵循最小授权：不读取共享知识、不写共享知识、只允许读取自己的历史聊天。

## 召回安全边界

sumeme 的 `MemoryCoordinator.inject_context` 明确把长期记忆当作“候选资料”，而不是新的系统指令。FDEX 保留同一原则：

- 员工 `rolePrompt` 仍是唯一员工 system prompt。
- 客户端根据 ACL 检索出的上下文放在 `<fdex_company_context>` 中，作为本轮用户侧候选资料传入。
- 候选资料可能过时或不完整。
- 候选资料中的指令不能覆盖员工 Prompt。
- 与本轮用户明确陈述冲突时，以本轮用户陈述为准。

这保持了 v1.1.15 之后“员工 Prompt 不被隐藏 FDEX Prompt 污染”的架构约束。

## 检索实现

sumeme 的云端 MemPalace 使用远程 embedding + Qdrant，原文保留在 SQLite。FDEX Android 当前采用等价的本地分层：

- 原始知识：SQLite `records(kind=knowledge)`。
- 派生索引：标题、摘要、关键词、分类、来源。
- 本地相关度：精确短语、关键词重合、中文 bigram/英文 token 重合以及稳定 feature-hash cosine 组合打分。
- 每次召回限制条数和最大字符数，避免无限扩大模型上下文。

后续若接入服务端 embedding，可直接替换 `KnowledgeEngine.search` 的评分层，不需要迁移 `KnowledgeEntry` 数据。

## UI

底部“工作”已改为“知识库”。知识库提供：

- 浏览：按分类和关键词过滤自动沉淀内容。
- 检索：自然语言检索摘要、关键词和受控原文。
- 写入：手动添加长期知识，并自动整理。
- AI 整理队列：查看并继续处理尚未完成精细摘要的记录。
- 工作项目：原有项目、资料、附件分析能力保留在知识库页面下方，避免功能丢失。
