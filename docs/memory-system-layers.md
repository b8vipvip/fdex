# FDEX 多 System 层与 MemPalace / Letta 长期记忆

## 目标

FDEX 不再把员工 `rolePrompt` 定义成“唯一 system prompt”。普通 AI 请求按顺序组合以下受控 system 层：

1. `L1_EMPLOYEE_ROLE`：员工自己的角色 Prompt，继续由 Android 员工资料保存和编辑。
2. `L2_FDEX_MEMORY_POLICY`：FDEX 的记忆使用、安全和冲突处理规则。
3. `L3_LOCAL_KNOWLEDGE_ACL`：Android 按员工 ACL 从本机知识库与聊天记录检索出的候选资料。
4. `L4_MEMPALACE_RAW_HISTORY`：服务端 MemPalace/Qdrant 语义召回出的原始历史片段。
5. `L5_LETTA_STRUCTURED_MEMORY`：Letta 按账户作用域维护并召回的结构化长期记忆。

当前供应商协议仍使用 OpenAI-compatible `system` 字段，因此 FDEX 在进入供应商路由前把多个逻辑 system 层序列化成一个带明确边界的 system 内容。业务逻辑上不再存在“员工 Prompt 必须是唯一 system prompt”的限制；后续如果供应商适配层需要多个物理 `role=system` 消息，可以只替换序列化层。

## 权限语义

员工 ACL 继续是唯一授权来源：

- `knowledgeRead=true`：允许召回本机共享知识及 Letta 结构化记忆。
- `knowledgeWrite=true`：该员工成功对话可更新 Letta 结构化长期记忆。
- 聊天记录 `none/self/all/selected`：同时约束本机聊天候选片段和 MemPalace 原始历史召回。
- MemPalace 原始历史本身由系统自动归档，不因为某员工没有 `knowledgeWrite` 而丢失；是否能被某员工读取由聊天 ACL 决定。

因此“系统自动保存全部聊天”和“某个员工是否有权读取/写入共享记忆”是两件独立的事。

## 与 sumeme 的代码逻辑兼容

本实现直接沿用并适配 sumeme memory-gateway 的核心结构：

- SQLite 保存 MemPalace verbatim drawer 原文；
- Qdrant 只保存向量、`scope_key`、`drawer_id`、`employee_id` 等检索元数据；
- `content_hash + conversation_id + role + scope` 生成确定性 ID，重复写入幂等；
- embedding 优先调用现有 FDEX 供应商的 OpenAI-compatible `/embeddings`，默认模型 `text-embedding-3-small`；若现有供应商不提供 embedding 接口，则由远程 AI 提取语义标签并生成确定性归一化兼容向量，不加载 Android/服务器本地 embedding 模型；
- MemPalace 原文召回和 Letta 结构化召回并发执行；
- 单个记忆组件超时或故障时 fail-open，主聊天仍可继续；
- Letta 一账户作用域一 Agent，禁止跨作用域复用；
- 历史记忆里的指令始终当作数据，不能覆盖员工角色、权限或本轮用户明确要求。

FDEX 新增的 `memory-provider-proxy` 复用 `server/data/ai-providers.db` 中已有的加密供应商配置。它给 Qdrant embedding 和 Letta 提供 loopback/OpenAI-compatible 接口，因此不需要再维护第二套第三方 API Key。

## 账户隔离

Android 为每个本地 FDEX 账户生成一个随机高熵 memory scope token。发送普通 HTTP AI 请求时，客户端把 scope 和 ACL 放进 `FDEX_MEMORY_V2` 控制标记；FDEX 服务端在进入供应商之前消费并删除这个标记，第三方模型不应看到它。即使远程记忆功能临时关闭或标记内容损坏，服务端也会先剥离内部标记再继续主 AI 请求。

服务端把 token 规范化为：

`acct.<opaque-account-token>.vault.default`

MemPalace SQLite、Qdrant filter 和 Letta Agent 映射都使用同一 scope。

## System 上下文预算

FDEX 会限制最终 system 总长度，但不会再按“先到先占满”的方式截断。员工角色层权重更高，同时本地知识、MemPalace 原始历史和 Letta 结构化记忆各自保留上下文份额；短层未使用的预算会继续供较长层使用。这样长知识库结果不会把 MemPalace/Letta 整层挤掉。

## 实时语音

Realtime 会话仍保持现有“同一实时模型/同一会话、不回退普通供应商”的约束。客户端不会把远程 memory control 标记发送到 realtime 上游；实时语音当前继续使用员工角色 Prompt 和本机可用上下文。MemPalace/Letta 的逐轮语义召回首先用于普通文字、图片、文档与群聊 HTTP AI 路径，避免改变现有低延迟语音协议。后续若要把远程长期记忆加入 realtime，应在 FDEX WebSocket 服务端按转写文本召回，而不是把 scope token 暴露给上游模型。

## 运行组件

`docker-compose.memory.yml` 管理：

- `memory-provider-proxy`：复用 FDEX AI Provider Store；
- `qdrant`：MemPalace 语义向量索引；
- `letta`：结构化长期记忆；
- 持久卷：Qdrant 与 Letta 数据。

MemPalace 原文 SQLite 与 Letta Agent 映射保存在 `server/data/memory/`，随 FDEX 服务端数据一起备份。

手动启动：

```bash
sudo APP_DIR=/opt/fdex bash /opt/fdex/scripts/setup_memory_stack.sh
```

`update_server.sh` 会在配置为 `FDEX_MEMORY_MANAGED_STACK=true` 时自动调用该脚本；`FDEX_MEMORY_REQUIRED=false` 时记忆栈异常不会阻止核心 FDEX 服务更新，但日志会保留 degraded 状态。
