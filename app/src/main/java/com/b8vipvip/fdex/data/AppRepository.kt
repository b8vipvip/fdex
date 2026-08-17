package com.b8vipvip.fdex.data

import android.content.Context
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.time.Instant


data class Profile(
    val name: String,
    val email: String,
    val companyName: String,
    val industry: String,
    val professionalLevel: String = "business",
    val autoCompanyMode: Boolean = false,
)

data class Employee(
    val id: Long,
    val name: String,
    val department: String,
    val position: String,
    val rolePrompt: String,
    val industry: String = "",
    val materialManager: Boolean = false,
    val active: Boolean = true,
)

data class ChatMessage(
    val id: Long,
    val employeeId: Long,
    val role: String,
    val content: String,
    val createdAt: String,
    val deleted: Boolean = false,
)

data class Project(
    val id: Long,
    val title: String,
    val description: String,
    val professionalLevel: String,
    val storageMode: String,
    val retentionPolicy: String,
    val allowThirdPartyAi: Boolean,
    val autoDesensitize: Boolean,
    val status: String,
    val requirementScore: Int,
    val autoOperation: Boolean,
    val createdAt: String,
    val updatedAt: String,
)

data class ProjectNote(val id: Long, val projectId: Long, val content: String, val createdAt: String)

data class ProjectAsset(
    val id: Long,
    val projectId: Long,
    val name: String,
    val uri: String,
    val size: Long,
    val mimeType: String,
    val status: String,
    val privacyDecision: String,
    val analysis: String,
    val createdAt: String,
)

data class Report(val id: Long, val projectId: Long, val title: String, val content: String, val createdAt: String)

data class WorkGroup(
    val id: Long,
    val name: String,
    val description: String,
    val projectId: Long?,
    val memberIds: List<Long>,
    val autoMode: Boolean,
    val createdAt: String,
    val updatedAt: String,
)

data class GroupMessage(
    val id: Long,
    val groupId: Long,
    val role: String,
    val employeeName: String,
    val content: String,
    val createdAt: String,
)

class AppRepository(context: Context) {
    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences("fdex_app_v2", Context.MODE_PRIVATE)
    private val database = FdexLocalDatabase(appContext)
    private val credentials = LocalCredentialStore()

    init {
        database.migrateLegacyIfNeeded(prefs)
    }

    private fun now() = Instant.now().toString()
    private fun nextId(): Long = database.nextId()

    private fun legacyHash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray())
        .joinToString("") { "%02x".format(it) }

    fun isLoggedIn(): Boolean = prefs.getBoolean("session", false)
    fun hasAccount(): Boolean = prefs.contains("account_email")

    fun register(name: String, email: String, password: String, companyName: String): Result<Profile> {
        if (name.isBlank()) return Result.failure(IllegalArgumentException("请输入姓名"))
        if (!email.contains("@")) return Result.failure(IllegalArgumentException("请输入正确邮箱"))
        if (password.length < 8) return Result.failure(IllegalArgumentException("密码至少 8 位"))
        val credentialRecord = runCatching { credentials.createRecord(password) }
            .getOrElse { return Result.failure(IllegalStateException("本机安全凭据初始化失败，请检查系统安全服务", it)) }
        val profile = Profile(name.trim(), email.trim().lowercase(), companyName.trim(), "")
        prefs.edit()
            .putString("account_email", profile.email)
            .putString(LocalCredentialStore.PREF_PASSWORD_RECORD, credentialRecord)
            .remove(LocalCredentialStore.LEGACY_PASSWORD_HASH)
            .putString("profile", profile.toJson().toString())
            .putBoolean("session", true)
            .apply()
        return Result.success(profile)
    }

    fun login(email: String, password: String): Result<Profile> {
        val normalizedEmail = email.trim().lowercase()
        if (prefs.getString("account_email", "") != normalizedEmail) {
            return Result.failure(IllegalArgumentException("邮箱或密码错误"))
        }

        val modernRecord = prefs.getString(LocalCredentialStore.PREF_PASSWORD_RECORD, "").orEmpty()
        val verified = if (modernRecord.isNotBlank()) {
            credentials.verify(modernRecord, password)
        } else {
            val legacy = prefs.getString(LocalCredentialStore.LEGACY_PASSWORD_HASH, "").orEmpty()
            val legacyOk = legacy.isNotBlank() && legacy == legacyHash(password)
            if (legacyOk) {
                // Existing users are upgraded on their first successful login;
                // no password reset or destructive account migration is needed.
                runCatching { credentials.createRecord(password) }.onSuccess { upgraded ->
                    prefs.edit()
                        .putString(LocalCredentialStore.PREF_PASSWORD_RECORD, upgraded)
                        .remove(LocalCredentialStore.LEGACY_PASSWORD_HASH)
                        .apply()
                }
            }
            legacyOk
        }

        if (!verified) return Result.failure(IllegalArgumentException("邮箱或密码错误"))
        prefs.edit().putBoolean("session", true).apply()
        return Result.success(profile())
    }

    fun logout() = prefs.edit().putBoolean("session", false).apply()

    fun profile(): Profile {
        val fallback = Profile("我", prefs.getString("account_email", "") ?: "", "我的 AI 公司", "")
        return runCatching { profileFromJson(JSONObject(prefs.getString("profile", "{}"))) }.getOrDefault(fallback)
    }

    fun updateProfile(profile: Profile) {
        prefs.edit().putString("profile", profile.toJson().toString()).apply()
    }

    fun employees(activeOnly: Boolean = true): List<Employee> = database
        .query(FdexLocalDatabase.KIND_EMPLOYEE)
        .mapNotNull { runCatching { employeeFromJson(it) }.getOrNull() }
        .filter { !activeOnly || it.active }
        .sortedBy { it.id }

    fun addEmployee(name: String, department: String, position: String, prompt: String, industry: String = ""): Employee {
        val item = Employee(nextId(), name.trim(), department.trim(), position.trim(), prompt.trim(), industry.trim())
        saveEmployee(item)
        return item
    }

    fun updateEmployee(employee: Employee) = saveEmployee(employee)

    private fun saveEmployee(employee: Employee) {
        database.upsert(
            FdexLocalDatabase.KIND_EMPLOYEE,
            employee.id,
            null,
            idSort(employee.id),
            employee.toJson(),
        )
    }

    fun bulkAddEmployees(industry: String) {
        val existing = employees().map { it.position }.toSet()
        listOf(
            Triple("小运", "运营中心", "运营主管"),
            Triple("小销", "销售中心", "销售顾问"),
            Triple("小创", "市场中心", "内容策划"),
            Triple("小数", "数据中心", "数据分析师"),
        ).filter { it.third !in existing }.forEach { (name, dep, pos) ->
            addEmployee(name, dep, pos, "", industry)
        }
    }

    fun resignEmployee(id: Long) {
        employee(id)?.takeIf { !it.materialManager }?.let { saveEmployee(it.copy(active = false)) }
    }

    fun employee(id: Long): Employee? = employees(activeOnly = false).firstOrNull { it.id == id }

    fun messages(employeeId: Long, includeDeleted: Boolean = false): List<ChatMessage> = database
        .query(FdexLocalDatabase.KIND_MESSAGE, employeeId)
        .mapNotNull { runCatching { messageFromJson(it) }.getOrNull() }
        .filter { includeDeleted || !it.deleted }
        .sortedBy { it.id }

    fun allDeletedMessages(): List<ChatMessage> = database
        .query(FdexLocalDatabase.KIND_MESSAGE)
        .mapNotNull { runCatching { messageFromJson(it) }.getOrNull() }
        .filter { it.deleted }
        .sortedByDescending { it.id }

    fun addMessage(employeeId: Long, role: String, content: String): ChatMessage {
        val item = ChatMessage(nextId(), employeeId, role, content, now())
        saveMessage(item)
        return item
    }

    private fun saveMessage(message: ChatMessage) {
        database.upsert(
            FdexLocalDatabase.KIND_MESSAGE,
            message.id,
            message.employeeId,
            idSort(message.id),
            message.toJson(),
        )
    }

    fun clearMessages(employeeId: Long) {
        messages(employeeId, includeDeleted = true).forEach { saveMessage(it.copy(deleted = true)) }
    }

    fun restoreDeletedMessages() {
        allDeletedMessages().forEach { saveMessage(it.copy(deleted = false)) }
    }

    fun projects(): List<Project> = database
        .query(FdexLocalDatabase.KIND_PROJECT)
        .mapNotNull { runCatching { projectFromJson(it) }.getOrNull() }
        .sortedByDescending { it.updatedAt }

    fun project(id: Long): Project? = projects().firstOrNull { it.id == id }

    fun createProject(
        title: String,
        description: String,
        professionalLevel: String,
        storageMode: String,
        retentionPolicy: String,
        allowAi: Boolean,
        autoDesensitize: Boolean,
        startAuto: Boolean,
    ): Project {
        val t = now()
        val score = (25 + description.length / 80).coerceIn(25, 90)
        val project = Project(
            nextId(),
            title.trim(),
            description.trim(),
            professionalLevel,
            storageMode,
            retentionPolicy,
            allowAi,
            autoDesensitize,
            "created",
            score,
            startAuto,
            t,
            t,
        )
        updateProject(project)
        if (startAuto) {
            val group = createGroup(
                "${project.title} · 工作群",
                "公司自动运营工作群",
                project.id,
                employees().map { it.id },
                true,
            )
            addGroupMessage(group.id, "system", "", "公司自动运营已启动，AI 团队正在检查需求并准备分工。")
        }
        return project
    }

    fun updateProject(project: Project) {
        database.upsert(
            FdexLocalDatabase.KIND_PROJECT,
            project.id,
            null,
            project.updatedAt,
            project.toJson(),
        )
    }

    fun notes(projectId: Long): List<ProjectNote> = database
        .query(FdexLocalDatabase.KIND_NOTE, projectId)
        .mapNotNull { runCatching { noteFromJson(it) }.getOrNull() }
        .sortedBy { it.id }

    fun addNote(projectId: Long, content: String): ProjectNote {
        val note = ProjectNote(nextId(), projectId, content.trim(), now())
        database.upsert(FdexLocalDatabase.KIND_NOTE, note.id, projectId, idSort(note.id), note.toJson())
        project(projectId)?.let {
            updateProject(it.copy(requirementScore = (it.requirementScore + 5).coerceAtMost(100), updatedAt = now()))
        }
        return note
    }

    fun assets(projectId: Long): List<ProjectAsset> = database
        .query(FdexLocalDatabase.KIND_ASSET, projectId)
        .mapNotNull { runCatching { assetFromJson(it) }.getOrNull() }
        .sortedByDescending { it.id }

    fun addAsset(projectId: Long, name: String, uri: Uri, size: Long, mime: String): ProjectAsset {
        val item = ProjectAsset(nextId(), projectId, name, uri.toString(), size, mime, "uploaded", "", "", now())
        updateAsset(item)
        return item
    }

    fun updateAsset(item: ProjectAsset) {
        database.upsert(FdexLocalDatabase.KIND_ASSET, item.id, item.projectId, idSort(item.id), item.toJson())
    }

    fun reports(projectId: Long): List<Report> = database
        .query(FdexLocalDatabase.KIND_REPORT, projectId)
        .mapNotNull { runCatching { reportFromJson(it) }.getOrNull() }
        .sortedByDescending { it.id }

    fun addReport(projectId: Long, title: String, content: String): Report {
        val report = Report(nextId(), projectId, title, content, now())
        database.upsert(FdexLocalDatabase.KIND_REPORT, report.id, projectId, idSort(report.id), report.toJson())
        project(projectId)?.let { updateProject(it.copy(status = "generated", updatedAt = now())) }
        return report
    }

    fun groups(): List<WorkGroup> = database
        .query(FdexLocalDatabase.KIND_GROUP)
        .mapNotNull { runCatching { groupFromJson(it) }.getOrNull() }
        .sortedByDescending { it.updatedAt }

    fun group(id: Long): WorkGroup? = groups().firstOrNull { it.id == id }

    fun createGroup(
        name: String,
        description: String,
        projectId: Long?,
        memberIds: List<Long>,
        autoMode: Boolean,
    ): WorkGroup {
        val t = now()
        val group = WorkGroup(nextId(), name.trim(), description.trim(), projectId, memberIds.distinct(), autoMode, t, t)
        saveGroup(group)
        return group
    }

    private fun saveGroup(group: WorkGroup) {
        database.upsert(
            FdexLocalDatabase.KIND_GROUP,
            group.id,
            group.projectId,
            group.updatedAt,
            group.toJson(),
        )
    }

    fun groupMessages(groupId: Long): List<GroupMessage> = database
        .query(FdexLocalDatabase.KIND_GROUP_MESSAGE, groupId)
        .mapNotNull { runCatching { groupMessageFromJson(it) }.getOrNull() }
        .sortedBy { it.id }

    fun addGroupMessage(groupId: Long, role: String, employeeName: String, content: String): GroupMessage {
        val message = GroupMessage(nextId(), groupId, role, employeeName, content, now())
        database.upsert(
            FdexLocalDatabase.KIND_GROUP_MESSAGE,
            message.id,
            groupId,
            idSort(message.id),
            message.toJson(),
        )
        group(groupId)?.let { saveGroup(it.copy(updatedAt = now())) }
        return message
    }

    fun resetAll() {
        database.clearAll()
        credentials.deleteDeviceKey()
        prefs.edit().clear().apply()
    }

    private fun idSort(id: Long): String = id.toString().padStart(20, '0')
}

private fun Profile.toJson() = JSONObject()
    .put("name", name)
    .put("email", email)
    .put("company", companyName)
    .put("industry", industry)
    .put("level", professionalLevel)
    .put("auto", autoCompanyMode)

private fun profileFromJson(o: JSONObject) = Profile(
    o.optString("name", "我"),
    o.optString("email"),
    o.optString("company", "我的 AI 公司"),
    o.optString("industry"),
    o.optString("level", "business"),
    o.optBoolean("auto"),
)

private fun Employee.toJson() = JSONObject()
    .put("id", id)
    .put("name", name)
    .put("dep", department)
    .put("pos", position)
    .put("prompt", rolePrompt)
    .put("industry", industry)
    .put("manager", materialManager)
    .put("active", active)

private fun employeeFromJson(o: JSONObject) = Employee(
    o.getLong("id"),
    o.optString("name"),
    o.optString("dep"),
    o.optString("pos"),
    o.optString("prompt"),
    o.optString("industry"),
    o.optBoolean("manager"),
    o.optBoolean("active", true),
)

private fun ChatMessage.toJson() = JSONObject()
    .put("id", id)
    .put("employee", employeeId)
    .put("role", role)
    .put("content", content)
    .put("at", createdAt)
    .put("deleted", deleted)

private fun messageFromJson(o: JSONObject) = ChatMessage(
    o.getLong("id"),
    o.getLong("employee"),
    o.optString("role"),
    o.optString("content"),
    o.optString("at"),
    o.optBoolean("deleted"),
)

private fun Project.toJson() = JSONObject()
    .put("id", id)
    .put("title", title)
    .put("desc", description)
    .put("level", professionalLevel)
    .put("storage", storageMode)
    .put("retention", retentionPolicy)
    .put("allowAi", allowThirdPartyAi)
    .put("desensitize", autoDesensitize)
    .put("status", status)
    .put("score", requirementScore)
    .put("auto", autoOperation)
    .put("created", createdAt)
    .put("updated", updatedAt)

private fun projectFromJson(o: JSONObject) = Project(
    o.getLong("id"),
    o.optString("title"),
    o.optString("desc"),
    o.optString("level", "business"),
    o.optString("storage", "hybrid"),
    o.optString("retention", "keep_forever"),
    o.optBoolean("allowAi", true),
    o.optBoolean("desensitize", true),
    o.optString("status", "created"),
    o.optInt("score", 25),
    o.optBoolean("auto"),
    o.optString("created"),
    o.optString("updated"),
)

private fun ProjectNote.toJson() = JSONObject()
    .put("id", id)
    .put("project", projectId)
    .put("content", content)
    .put("at", createdAt)

private fun noteFromJson(o: JSONObject) = ProjectNote(
    o.getLong("id"),
    o.getLong("project"),
    o.optString("content"),
    o.optString("at"),
)

private fun ProjectAsset.toJson() = JSONObject()
    .put("id", id)
    .put("project", projectId)
    .put("name", name)
    .put("uri", uri)
    .put("size", size)
    .put("mime", mimeType)
    .put("status", status)
    .put("privacy", privacyDecision)
    .put("analysis", analysis)
    .put("at", createdAt)

private fun assetFromJson(o: JSONObject) = ProjectAsset(
    o.getLong("id"),
    o.getLong("project"),
    o.optString("name"),
    o.optString("uri"),
    o.optLong("size"),
    o.optString("mime"),
    o.optString("status", "uploaded"),
    o.optString("privacy"),
    o.optString("analysis"),
    o.optString("at"),
)

private fun Report.toJson() = JSONObject()
    .put("id", id)
    .put("project", projectId)
    .put("title", title)
    .put("content", content)
    .put("at", createdAt)

private fun reportFromJson(o: JSONObject) = Report(
    o.getLong("id"),
    o.getLong("project"),
    o.optString("title"),
    o.optString("content"),
    o.optString("at"),
)

private fun WorkGroup.toJson() = JSONObject()
    .put("id", id)
    .put("name", name)
    .put("desc", description)
    .put("project", projectId ?: JSONObject.NULL)
    .put("members", JSONArray(memberIds))
    .put("auto", autoMode)
    .put("created", createdAt)
    .put("updated", updatedAt)

private fun groupFromJson(o: JSONObject): WorkGroup {
    val ids = mutableListOf<Long>()
    val members = o.optJSONArray("members") ?: JSONArray()
    for (index in 0 until members.length()) ids += members.optLong(index)
    return WorkGroup(
        o.getLong("id"),
        o.optString("name"),
        o.optString("desc"),
        if (o.isNull("project")) null else o.optLong("project"),
        ids,
        o.optBoolean("auto"),
        o.optString("created"),
        o.optString("updated"),
    )
}

private fun GroupMessage.toJson() = JSONObject()
    .put("id", id)
    .put("group", groupId)
    .put("role", role)
    .put("employee", employeeName)
    .put("content", content)
    .put("at", createdAt)

private fun groupMessageFromJson(o: JSONObject) = GroupMessage(
    o.getLong("id"),
    o.getLong("group"),
    o.optString("role"),
    o.optString("employee"),
    o.optString("content"),
    o.optString("at"),
)
