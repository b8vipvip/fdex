package com.b8vipvip.fdex.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

class KnowledgeStore(context: Context) {
    private val appContext = context.applicationContext
    private val database = FdexLocalDatabase(appContext)
    private val metaPrefs = appContext.getSharedPreferences("fdex_knowledge_meta_v1", Context.MODE_PRIVATE)

    fun permissionsFor(employeeId: Long): EmployeePermissions {
        val json = database.query(FdexLocalDatabase.KIND_EMPLOYEE_PERMISSION, employeeId).firstOrNull()
            ?: return EmployeePermissions()
        val selected = mutableListOf<Long>()
        val ids = json.optJSONArray("readable_employee_ids") ?: JSONArray()
        for (index in 0 until ids.length()) {
            ids.optLong(index).takeIf { it > 0 }?.let(selected::add)
        }
        return EmployeePermissions(
            knowledgeRead = json.optBoolean("knowledge_read", false),
            knowledgeWrite = json.optBoolean("knowledge_write", false),
            chatAccessMode = EmployeeChatAccess.normalize(json.optString("chat_access_mode", EmployeeChatAccess.SELF)),
            readableEmployeeIds = selected.distinct(),
        )
    }

    fun savePermissions(employeeId: Long, permissions: EmployeePermissions) {
        val normalized = permissions.copy(
            chatAccessMode = EmployeeChatAccess.normalize(permissions.chatAccessMode),
            readableEmployeeIds = permissions.readableEmployeeIds.filter { it > 0 && it != employeeId }.distinct(),
        )
        val payload = JSONObject()
            .put("employee_id", employeeId)
            .put("knowledge_read", normalized.knowledgeRead)
            .put("knowledge_write", normalized.knowledgeWrite)
            .put("chat_access_mode", normalized.chatAccessMode)
            .put("readable_employee_ids", JSONArray(normalized.readableEmployeeIds))
        database.upsert(
            FdexLocalDatabase.KIND_EMPLOYEE_PERMISSION,
            employeeId,
            employeeId,
            idSort(employeeId),
            payload,
        )
    }

    /**
     * Builds an opaque, server-consumed control marker for MemPalace/Letta. The random
     * account scope never contains the email/password and is stable only inside this app.
     * The server removes this marker before any third-party AI provider sees the prompt.
     */
    fun remoteMemoryControl(
        repo: AppRepository,
        employee: Employee,
        conversationId: String,
    ): String {
        val permissions = permissionsFor(employee.id)
        val localScope = scopeKey(repo)
        val preferenceKey = "remote_memory_scope_" + KnowledgeEngine.contentHash(localScope).take(20)
        var token = metaPrefs.getString(preferenceKey, "").orEmpty().trim()
        if (token.length < 24) {
            token = java.util.UUID.randomUUID().toString().replace("-", "") +
                java.util.UUID.randomUUID().toString().replace("-", "")
            metaPrefs.edit().putString(preferenceKey, token).commit()
        }
        val payload = JSONObject()
            .put("scope", token)
            .put("conversation_id", conversationId.take(512))
            .put("employee_id", employee.id.toString())
            .put("knowledge_read", permissions.knowledgeRead)
            .put("knowledge_write", permissions.knowledgeWrite)
            .put("chat_access_mode", EmployeeChatAccess.normalize(permissions.chatAccessMode))
            .put("readable_employee_ids", JSONArray(permissions.readableEmployeeIds))
            .toString()
        val encoded = android.util.Base64.encodeToString(
            payload.toByteArray(Charsets.UTF_8),
            android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or android.util.Base64.NO_PADDING,
        )
        return "[[FDEX_MEMORY_V2:$encoded]]"
    }

    fun entries(includeArchived: Boolean = false): List<KnowledgeEntry> = database
        .query(FdexLocalDatabase.KIND_KNOWLEDGE)
        .mapNotNull { runCatching { knowledgeFromJson(it) }.getOrNull() }
        .filter { includeArchived || !it.archived }
        .sortedByDescending { it.updatedAt }

    fun entry(id: Long): KnowledgeEntry? = entries(includeArchived = true).firstOrNull { it.id == id }

    fun pendingEntries(limit: Int = 20): List<KnowledgeEntry> = entries()
        .filter { it.needsEnrichment }
        .sortedByDescending { it.createdAt }
        .take(limit)

    fun update(entry: KnowledgeEntry) = save(entry)

    fun archive(id: Long) {
        entry(id)?.let { save(it.copy(archived = true, updatedAt = now())) }
    }

    fun addManual(
        repo: AppRepository,
        title: String,
        content: String,
        room: String = KnowledgeRooms.GENERAL,
        keywords: List<String> = emptyList(),
    ): KnowledgeEntry {
        val raw = content.trim()
        val fallback = KnowledgeEngine.fallbackDraft(raw)
        val timestamp = now()
        val scope = scopeKey(repo)
        val hash = KnowledgeEngine.contentHash("$scope|manual|$raw")
        entries(includeArchived = true).firstOrNull { it.contentHash == hash && it.source == "manual" }?.let { return it }
        val item = KnowledgeEntry(
            id = database.nextId(),
            scopeKey = scope,
            wing = "company",
            room = KnowledgeRooms.normalize(room),
            title = title.trim().ifBlank { fallback.title },
            summary = fallback.summary,
            keywords = (keywords + fallback.keywords).map(String::trim).filter(String::isNotBlank).distinct().take(12),
            rawText = raw,
            conversationId = "manual",
            source = "manual",
            sourceEmployeeId = null,
            sourceEmployeeName = "",
            sourceMessageIds = emptyList(),
            contentHash = hash,
            sharedForAgents = true,
            needsEnrichment = true,
            createdAt = timestamp,
            updatedAt = timestamp,
        )
        save(item)
        return item
    }

    fun rememberEmployeeExchange(
        repo: AppRepository,
        employeeId: Long,
        user: ChatMessage,
        assistant: ChatMessage,
        allowSharing: Boolean = true,
    ): KnowledgeEntry {
        val employee = repo.employee(employeeId)
        val name = employee?.name.orEmpty().ifBlank { "AI 员工" }
        return rememberExchange(
            repo = repo,
            conversationId = "employee:$employeeId",
            source = "employee_chat:$employeeId",
            sourceEmployeeId = employeeId,
            sourceEmployeeName = name,
            sourceMessageIds = listOf(user.id, assistant.id),
            userText = cleanChatContent(user.content),
            assistantText = cleanChatContent(assistant.content),
            allowSharing = allowSharing,
        )
    }

    fun rememberGroupExchange(
        repo: AppRepository,
        groupId: Long,
        targetEmployeeId: Long?,
        targetEmployeeName: String,
        user: GroupMessage,
        assistant: GroupMessage,
        allowSharing: Boolean = true,
    ): KnowledgeEntry = rememberExchange(
        repo = repo,
        conversationId = "group:$groupId",
        source = "group_chat:$groupId",
        sourceEmployeeId = targetEmployeeId,
        sourceEmployeeName = targetEmployeeName,
        sourceMessageIds = listOf(user.id, assistant.id),
        userText = cleanChatContent(user.content),
        assistantText = cleanChatContent(assistant.content),
        allowSharing = allowSharing,
    )

    /**
     * Imports historical private/group exchanges once for each account scope. New conversations
     * are archived immediately, so rescanning the full history on every prompt would only add cost.
     * The import itself remains content-hash idempotent, therefore a crash before the marker is set
     * can safely retry on the next launch/send.
     */
    fun backfillIfNeeded(repo: AppRepository): Int {
        val marker = "history_backfilled_${scopeKey(repo)}"
        if (metaPrefs.getBoolean(marker, false)) return 0
        val created = backfill(repo)
        metaPrefs.edit().putBoolean(marker, true).apply()
        return created
    }

    fun backfill(repo: AppRepository): Int {
        var created = 0
        repo.employees(activeOnly = false).forEach { employee ->
            var pendingUser: ChatMessage? = null
            repo.messages(employee.id).forEach { message ->
                when (message.role) {
                    "user" -> pendingUser = message
                    else -> {
                        val user = pendingUser ?: return@forEach
                        val before = entries(includeArchived = true).size
                        rememberEmployeeExchange(repo, employee.id, user, message, allowSharing = true)
                        if (entries(includeArchived = true).size > before) created++
                        pendingUser = null
                    }
                }
            }
        }

        val employeeByName = repo.employees(activeOnly = false).associateBy { it.name }
        repo.groups().forEach { group ->
            var pendingUser: GroupMessage? = null
            repo.groupMessages(group.id).forEach { message ->
                when (message.role) {
                    "user" -> pendingUser = message
                    "employee" -> {
                        val user = pendingUser ?: return@forEach
                        val target = employeeByName[message.employeeName]
                        val before = entries(includeArchived = true).size
                        rememberGroupExchange(
                            repo = repo,
                            groupId = group.id,
                            targetEmployeeId = target?.id,
                            targetEmployeeName = message.employeeName,
                            user = user,
                            assistant = message,
                            allowSharing = true,
                        )
                        if (entries(includeArchived = true).size > before) created++
                        pendingUser = null
                    }
                }
            }
        }
        return created
    }

    fun recallForEmployee(
        repo: AppRepository,
        employee: Employee,
        query: String,
        maxChars: Int = 12_000,
    ): String {
        backfillIfNeeded(repo)
        val permissions = permissionsFor(employee.id)
        val allEntries = entries()
        val sections = mutableListOf<String>()
        val used = mutableSetOf<Long>()

        if (permissions.knowledgeRead) {
            val shared = allEntries.filter { it.sharedForAgents }
            val hits = KnowledgeEngine.search(shared, query, limit = 6)
            if (hits.isNotEmpty()) {
                val text = hits.joinToString("\n") { hit ->
                    used += hit.entry.id
                    val entry = hit.entry
                    "- [${KnowledgeRooms.label(entry.room)}] ${entry.title}：${entry.summary}" +
                        entry.keywords.takeIf { it.isNotEmpty() }?.joinToString(prefix = "（关键词：", postfix = "）")
                            .orEmpty()
                }
                sections += "知识库候选资料：\n$text"
            }
        }

        val allowedEmployeeIds = when (permissions.chatAccessMode) {
            EmployeeChatAccess.NONE -> emptySet()
            EmployeeChatAccess.ALL -> repo.employees(activeOnly = false).map { it.id }.toSet()
            EmployeeChatAccess.SELECTED -> permissions.readableEmployeeIds.toSet()
            else -> setOf(employee.id)
        }
        if (allowedEmployeeIds.isNotEmpty()) {
            val chatEntries = allEntries.filter { entry ->
                entry.id !in used &&
                    entry.sourceEmployeeId in allowedEmployeeIds &&
                    (entry.source.startsWith("employee_chat:") || entry.source.startsWith("group_chat:"))
            }
            val hits = KnowledgeEngine.search(chatEntries, query, limit = 6)
            if (hits.isNotEmpty()) {
                sections += "获准读取的聊天记录候选片段：\n" + hits.joinToString("\n\n") { hit ->
                    val entry = hit.entry
                    val owner = entry.sourceEmployeeName.ifBlank { "员工" }
                    "[${owner} / ${entry.conversationId}]\n${entry.rawText.take(1800)}"
                }
            }
        }

        return sections.joinToString("\n\n").take(maxChars)
    }

    private fun rememberExchange(
        repo: AppRepository,
        conversationId: String,
        source: String,
        sourceEmployeeId: Long?,
        sourceEmployeeName: String,
        sourceMessageIds: List<Long>,
        userText: String,
        assistantText: String,
        allowSharing: Boolean,
    ): KnowledgeEntry {
        val raw = buildString {
            append("用户：").append(userText.ifBlank { "（无文字内容）" })
            append("\n")
            append(sourceEmployeeName.ifBlank { "AI" }).append("：").append(assistantText.ifBlank { "（无文字回复）" })
        }
        val scope = scopeKey(repo)
        val hashBasis = buildString {
            append(scope).append('|').append(conversationId).append('|')
            append(sourceMessageIds.joinToString(",")).append('|').append(raw)
        }
        val hash = KnowledgeEngine.contentHash(hashBasis)
        entries(includeArchived = true).firstOrNull { it.contentHash == hash }?.let { return it }
        val fallback = KnowledgeEngine.fallbackDraft(raw, sourceEmployeeName)
        val timestamp = now()
        val canShare = sourceEmployeeId?.let { permissionsFor(it).knowledgeWrite } == true && allowSharing
        val item = KnowledgeEntry(
            id = database.nextId(),
            scopeKey = scope,
            wing = "company",
            room = fallback.room,
            title = fallback.title,
            summary = fallback.summary,
            keywords = fallback.keywords,
            rawText = raw,
            conversationId = conversationId,
            source = source,
            sourceEmployeeId = sourceEmployeeId,
            sourceEmployeeName = sourceEmployeeName,
            sourceMessageIds = sourceMessageIds,
            contentHash = hash,
            sharedForAgents = canShare,
            needsEnrichment = true,
            createdAt = timestamp,
            updatedAt = timestamp,
        )
        save(item)
        return item
    }

    private fun save(entry: KnowledgeEntry) {
        database.upsert(
            FdexLocalDatabase.KIND_KNOWLEDGE,
            entry.id,
            entry.sourceEmployeeId,
            entry.updatedAt,
            entry.toJson(),
        )
    }

    private fun scopeKey(repo: AppRepository): String {
        val account = repo.profile().email.trim().lowercase().ifBlank { "local" }
        return "fdex-account:${KnowledgeEngine.contentHash(account).take(24)}"
    }

    private fun cleanChatContent(value: String): String = value
        .replace(Regex("(?s)\\s*\\[\\[FDEX_ATTACHMENTS_V1:[A-Za-z0-9_\\-=]+]]\\s*$"), "")
        .trim()

    private fun now(): String = Instant.now().toString()
    private fun idSort(id: Long): String = id.toString().padStart(20, '0')
}

private fun KnowledgeEntry.toJson() = JSONObject()
    .put("id", id)
    .put("scope_key", scopeKey)
    .put("wing", wing)
    .put("room", room)
    .put("title", title)
    .put("summary", summary)
    .put("keywords", JSONArray(keywords))
    .put("raw_text", rawText)
    .put("conversation_id", conversationId)
    .put("source", source)
    .put("source_employee_id", sourceEmployeeId ?: JSONObject.NULL)
    .put("source_employee_name", sourceEmployeeName)
    .put("source_message_ids", JSONArray(sourceMessageIds))
    .put("content_hash", contentHash)
    .put("shared_for_agents", sharedForAgents)
    .put("needs_enrichment", needsEnrichment)
    .put("created_at", createdAt)
    .put("updated_at", updatedAt)
    .put("archived", archived)

private fun knowledgeFromJson(o: JSONObject): KnowledgeEntry {
    val keywords = mutableListOf<String>()
    val keywordArray = o.optJSONArray("keywords") ?: JSONArray()
    for (index in 0 until keywordArray.length()) {
        keywordArray.optString(index).takeIf { it.isNotBlank() }?.let(keywords::add)
    }
    val messageIds = mutableListOf<Long>()
    val messageArray = o.optJSONArray("source_message_ids") ?: JSONArray()
    for (index in 0 until messageArray.length()) {
        messageArray.optLong(index).takeIf { it > 0 }?.let(messageIds::add)
    }
    return KnowledgeEntry(
        id = o.getLong("id"),
        scopeKey = o.optString("scope_key"),
        wing = o.optString("wing", "company"),
        room = KnowledgeRooms.normalize(o.optString("room", KnowledgeRooms.GENERAL)),
        title = o.optString("title"),
        summary = o.optString("summary"),
        keywords = keywords,
        rawText = o.optString("raw_text"),
        conversationId = o.optString("conversation_id"),
        source = o.optString("source"),
        sourceEmployeeId = if (o.isNull("source_employee_id")) null else o.optLong("source_employee_id").takeIf { it > 0 },
        sourceEmployeeName = o.optString("source_employee_name"),
        sourceMessageIds = messageIds,
        contentHash = o.optString("content_hash"),
        sharedForAgents = o.optBoolean("shared_for_agents", false),
        needsEnrichment = o.optBoolean("needs_enrichment", false),
        createdAt = o.optString("created_at"),
        updatedAt = o.optString("updated_at"),
        archived = o.optBoolean("archived", false),
    )
}
