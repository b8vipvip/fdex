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
    private val prefs = context.applicationContext.getSharedPreferences("fdex_app_v2", Context.MODE_PRIVATE)

    private fun now() = Instant.now().toString()
    private fun nextId(): Long {
        val value = prefs.getLong("next_id", 1000L) + 1L
        prefs.edit().putLong("next_id", value).apply()
        return value
    }
    private fun hash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    private fun readArray(key: String): JSONArray = runCatching { JSONArray(prefs.getString(key, "[]")) }.getOrDefault(JSONArray())
    private fun writeArray(key: String, value: JSONArray) = prefs.edit().putString(key, value.toString()).apply()

    fun isLoggedIn(): Boolean = prefs.getBoolean("session", false)
    fun hasAccount(): Boolean = prefs.contains("account_email")

    fun register(name: String, email: String, password: String, companyName: String): Result<Profile> {
        if (name.isBlank()) return Result.failure(IllegalArgumentException("请输入姓名"))
        if (!email.contains("@")) return Result.failure(IllegalArgumentException("请输入正确邮箱"))
        if (password.length < 8) return Result.failure(IllegalArgumentException("密码至少 8 位"))
        val profile = Profile(name.trim(), email.trim().lowercase(), companyName.trim(), "")
        prefs.edit()
            .putString("account_email", profile.email)
            .putString("account_password", hash(password))
            .putString("profile", profile.toJson().toString())
            .putBoolean("session", true)
            .apply()
        seedEmployees()
        return Result.success(profile)
    }

    fun login(email: String, password: String): Result<Profile> {
        val ok = prefs.getString("account_email", "") == email.trim().lowercase() &&
            prefs.getString("account_password", "") == hash(password)
        if (!ok) return Result.failure(IllegalArgumentException("邮箱或密码错误"))
        prefs.edit().putBoolean("session", true).apply()
        seedEmployees()
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

    fun employees(activeOnly: Boolean = true): List<Employee> {
        seedEmployees()
        val out = mutableListOf<Employee>()
        val a = readArray("employees")
        for (i in 0 until a.length()) {
            val item = employeeFromJson(a.getJSONObject(i))
            if (!activeOnly || item.active) out += item
        }
        return out
    }

    fun addEmployee(name: String, department: String, position: String, prompt: String, industry: String = ""): Employee {
        val item = Employee(nextId(), name.trim(), department.trim(), position.trim(), prompt.trim(), industry.trim())
        val a = readArray("employees"); a.put(item.toJson()); writeArray("employees", a)
        return item
    }

    fun bulkAddEmployees(industry: String) {
        val existing = employees().map { it.position }.toSet()
        listOf(
            Triple("小运", "运营中心", "运营主管"),
            Triple("小销", "销售中心", "销售顾问"),
            Triple("小创", "市场中心", "内容策划"),
            Triple("小数", "数据中心", "数据分析师"),
        ).filter { it.third !in existing }.forEach { (name, dep, pos) ->
            addEmployee(name, dep, pos, "你是${industry.ifBlank { "通用" }}行业的$pos，从岗位视角给出专业、可执行的建议。", industry)
        }
    }

    fun resignEmployee(id: Long) {
        val updated = employees(activeOnly = false).map {
            if (it.id == id && !it.materialManager) it.copy(active = false) else it
        }
        writeArray("employees", JSONArray().apply { updated.forEach { put(it.toJson()) } })
    }

    fun employee(id: Long): Employee? = employees(activeOnly = false).firstOrNull { it.id == id }

    fun messages(employeeId: Long, includeDeleted: Boolean = false): List<ChatMessage> {
        val out = mutableListOf<ChatMessage>(); val a = readArray("messages")
        for (i in 0 until a.length()) {
            val m = messageFromJson(a.getJSONObject(i))
            if (m.employeeId == employeeId && (includeDeleted || !m.deleted)) out += m
        }
        return out.sortedBy { it.id }
    }

    fun allDeletedMessages(): List<ChatMessage> {
        val out = mutableListOf<ChatMessage>(); val a = readArray("messages")
        for (i in 0 until a.length()) {
            val m = messageFromJson(a.getJSONObject(i)); if (m.deleted) out += m
        }
        return out.sortedByDescending { it.id }
    }

    fun addMessage(employeeId: Long, role: String, content: String): ChatMessage {
        val item = ChatMessage(nextId(), employeeId, role, content, now())
        val a = readArray("messages"); a.put(item.toJson()); writeArray("messages", a)
        return item
    }

    fun clearMessages(employeeId: Long) {
        val all = mutableListOf<ChatMessage>(); val a = readArray("messages")
        for (i in 0 until a.length()) {
            val m = messageFromJson(a.getJSONObject(i)); all += if (m.employeeId == employeeId) m.copy(deleted = true) else m
        }
        writeArray("messages", JSONArray().apply { all.forEach { put(it.toJson()) } })
    }

    fun restoreDeletedMessages() {
        val all = mutableListOf<ChatMessage>(); val a = readArray("messages")
        for (i in 0 until a.length()) all += messageFromJson(a.getJSONObject(i)).copy(deleted = false)
        writeArray("messages", JSONArray().apply { all.forEach { put(it.toJson()) } })
    }

    fun projects(): List<Project> {
        val out = mutableListOf<Project>(); val a = readArray("projects")
        for (i in 0 until a.length()) out += projectFromJson(a.getJSONObject(i))
        return out.sortedByDescending { it.updatedAt }
    }

    fun project(id: Long): Project? = projects().firstOrNull { it.id == id }

    fun createProject(
        title: String, description: String, professionalLevel: String, storageMode: String,
        retentionPolicy: String, allowAi: Boolean, autoDesensitize: Boolean, startAuto: Boolean,
    ): Project {
        val t = now()
        val score = (25 + description.length / 80).coerceIn(25, 90)
        val p = Project(nextId(), title.trim(), description.trim(), professionalLevel, storageMode, retentionPolicy, allowAi, autoDesensitize, "created", score, startAuto, t, t)
        val a = readArray("projects"); a.put(p.toJson()); writeArray("projects", a)
        if (startAuto) {
            val g = createGroup("${p.title} · 工作群", "公司自动运营工作群", p.id, employees().map { it.id }, true)
            addGroupMessage(g.id, "system", "", "公司自动运营已启动，AI 团队正在检查需求并准备分工。")
        }
        return p
    }

    fun updateProject(project: Project) {
        val all = projects().map { if (it.id == project.id) project else it }
        writeArray("projects", JSONArray().apply { all.forEach { put(it.toJson()) } })
    }

    fun notes(projectId: Long): List<ProjectNote> {
        val out = mutableListOf<ProjectNote>(); val a = readArray("notes")
        for (i in 0 until a.length()) {
            val n = noteFromJson(a.getJSONObject(i)); if (n.projectId == projectId) out += n
        }
        return out
    }

    fun addNote(projectId: Long, content: String): ProjectNote {
        val n = ProjectNote(nextId(), projectId, content.trim(), now())
        val a = readArray("notes"); a.put(n.toJson()); writeArray("notes", a)
        project(projectId)?.let { updateProject(it.copy(requirementScore = (it.requirementScore + 5).coerceAtMost(100), updatedAt = now())) }
        return n
    }

    fun assets(projectId: Long): List<ProjectAsset> {
        val out = mutableListOf<ProjectAsset>(); val a = readArray("assets")
        for (i in 0 until a.length()) {
            val item = assetFromJson(a.getJSONObject(i)); if (item.projectId == projectId) out += item
        }
        return out.sortedByDescending { it.id }
    }

    fun addAsset(projectId: Long, name: String, uri: Uri, size: Long, mime: String): ProjectAsset {
        val item = ProjectAsset(nextId(), projectId, name, uri.toString(), size, mime, "uploaded", "", "", now())
        val a = readArray("assets"); a.put(item.toJson()); writeArray("assets", a); return item
    }

    fun updateAsset(item: ProjectAsset) {
        val a = readArray("assets"); val out = JSONArray()
        for (i in 0 until a.length()) {
            val x = assetFromJson(a.getJSONObject(i)); out.put((if (x.id == item.id) item else x).toJson())
        }
        writeArray("assets", out)
    }

    fun reports(projectId: Long): List<Report> {
        val out = mutableListOf<Report>(); val a = readArray("reports")
        for (i in 0 until a.length()) {
            val r = reportFromJson(a.getJSONObject(i)); if (r.projectId == projectId) out += r
        }
        return out.sortedByDescending { it.id }
    }

    fun addReport(projectId: Long, title: String, content: String): Report {
        val r = Report(nextId(), projectId, title, content, now())
        val a = readArray("reports"); a.put(r.toJson()); writeArray("reports", a)
        project(projectId)?.let { updateProject(it.copy(status = "generated", updatedAt = now())) }
        return r
    }

    fun groups(): List<WorkGroup> {
        val out = mutableListOf<WorkGroup>(); val a = readArray("groups")
        for (i in 0 until a.length()) out += groupFromJson(a.getJSONObject(i))
        return out.sortedByDescending { it.updatedAt }
    }

    fun group(id: Long): WorkGroup? = groups().firstOrNull { it.id == id }

    fun createGroup(name: String, description: String, projectId: Long?, memberIds: List<Long>, autoMode: Boolean): WorkGroup {
        val t = now(); val g = WorkGroup(nextId(), name.trim(), description.trim(), projectId, memberIds.distinct(), autoMode, t, t)
        val a = readArray("groups"); a.put(g.toJson()); writeArray("groups", a); return g
    }

    fun groupMessages(groupId: Long): List<GroupMessage> {
        val out = mutableListOf<GroupMessage>(); val a = readArray("group_messages")
        for (i in 0 until a.length()) {
            val m = groupMessageFromJson(a.getJSONObject(i)); if (m.groupId == groupId) out += m
        }
        return out.sortedBy { it.id }
    }

    fun addGroupMessage(groupId: Long, role: String, employeeName: String, content: String): GroupMessage {
        val m = GroupMessage(nextId(), groupId, role, employeeName, content, now())
        val a = readArray("group_messages"); a.put(m.toJson()); writeArray("group_messages", a)
        val updated = groups().map { if (it.id == groupId) it.copy(updatedAt = now()) else it }
        writeArray("groups", JSONArray().apply { updated.forEach { put(it.toJson()) } })
        return m
    }

    fun resetAll() = prefs.edit().clear().apply()

    private fun seedEmployees() {
        if (readArray("employees").length() > 0) return
        val industry = profile().industry
        val items = listOf(
            Employee(nextId(), "小知", "资料中心", "资料管理员", "负责资料整理、知识检索、风险提醒和项目上下文管理。", industry, true),
            Employee(nextId(), "小策", "经营中心", "业务策划", "把目标拆成可以执行的商业方案、步骤和检查清单。", industry),
            Employee(nextId(), "小研", "研究中心", "行业研究员", "负责行业分析、竞品研究、信息验证与机会判断。", industry),
            Employee(nextId(), "小执", "项目中心", "执行经理", "负责把任务拆解、排期、跟进和形成阶段汇报。", industry),
        )
        writeArray("employees", JSONArray().apply { items.forEach { put(it.toJson()) } })
    }
}

private fun Profile.toJson() = JSONObject().put("name", name).put("email", email).put("company", companyName).put("industry", industry).put("level", professionalLevel).put("auto", autoCompanyMode)
private fun profileFromJson(o: JSONObject) = Profile(o.optString("name", "我"), o.optString("email"), o.optString("company", "我的 AI 公司"), o.optString("industry"), o.optString("level", "business"), o.optBoolean("auto"))
private fun Employee.toJson() = JSONObject().put("id", id).put("name", name).put("dep", department).put("pos", position).put("prompt", rolePrompt).put("industry", industry).put("manager", materialManager).put("active", active)
private fun employeeFromJson(o: JSONObject) = Employee(o.getLong("id"), o.optString("name"), o.optString("dep"), o.optString("pos"), o.optString("prompt"), o.optString("industry"), o.optBoolean("manager"), o.optBoolean("active", true))
private fun ChatMessage.toJson() = JSONObject().put("id", id).put("employee", employeeId).put("role", role).put("content", content).put("at", createdAt).put("deleted", deleted)
private fun messageFromJson(o: JSONObject) = ChatMessage(o.getLong("id"), o.getLong("employee"), o.optString("role"), o.optString("content"), o.optString("at"), o.optBoolean("deleted"))
private fun Project.toJson() = JSONObject().put("id", id).put("title", title).put("desc", description).put("level", professionalLevel).put("storage", storageMode).put("retention", retentionPolicy).put("allowAi", allowThirdPartyAi).put("desensitize", autoDesensitize).put("status", status).put("score", requirementScore).put("auto", autoOperation).put("created", createdAt).put("updated", updatedAt)
private fun projectFromJson(o: JSONObject) = Project(o.getLong("id"), o.optString("title"), o.optString("desc"), o.optString("level", "business"), o.optString("storage", "hybrid"), o.optString("retention", "keep_forever"), o.optBoolean("allowAi", true), o.optBoolean("desensitize", true), o.optString("status", "created"), o.optInt("score", 25), o.optBoolean("auto"), o.optString("created"), o.optString("updated"))
private fun ProjectNote.toJson() = JSONObject().put("id", id).put("project", projectId).put("content", content).put("at", createdAt)
private fun noteFromJson(o: JSONObject) = ProjectNote(o.getLong("id"), o.getLong("project"), o.optString("content"), o.optString("at"))
private fun ProjectAsset.toJson() = JSONObject().put("id", id).put("project", projectId).put("name", name).put("uri", uri).put("size", size).put("mime", mimeType).put("status", status).put("privacy", privacyDecision).put("analysis", analysis).put("at", createdAt)
private fun assetFromJson(o: JSONObject) = ProjectAsset(o.getLong("id"), o.getLong("project"), o.optString("name"), o.optString("uri"), o.optLong("size"), o.optString("mime"), o.optString("status", "uploaded"), o.optString("privacy"), o.optString("analysis"), o.optString("at"))
private fun Report.toJson() = JSONObject().put("id", id).put("project", projectId).put("title", title).put("content", content).put("at", createdAt)
private fun reportFromJson(o: JSONObject) = Report(o.getLong("id"), o.getLong("project"), o.optString("title"), o.optString("content"), o.optString("at"))
private fun WorkGroup.toJson() = JSONObject().put("id", id).put("name", name).put("desc", description).put("project", projectId ?: JSONObject.NULL).put("members", JSONArray(memberIds)).put("auto", autoMode).put("created", createdAt).put("updated", updatedAt)
private fun groupFromJson(o: JSONObject): WorkGroup { val ids = mutableListOf<Long>(); val a=o.optJSONArray("members")?:JSONArray(); for(i in 0 until a.length()) ids+=a.optLong(i); return WorkGroup(o.getLong("id"),o.optString("name"),o.optString("desc"),if(o.isNull("project"))null else o.optLong("project"),ids,o.optBoolean("auto"),o.optString("created"),o.optString("updated")) }
private fun GroupMessage.toJson() = JSONObject().put("id", id).put("group", groupId).put("role", role).put("employee", employeeName).put("content", content).put("at", createdAt)
private fun groupMessageFromJson(o: JSONObject) = GroupMessage(o.getLong("id"), o.getLong("group"), o.optString("role"), o.optString("employee"), o.optString("content"), o.optString("at"))
