package com.b8vipvip.fdex.network

import android.content.Context
import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit


data class AgentEventDto(val type: String, val message: String, val createdAt: String)
data class AgentGitHubConnectionDto(val id: Int, val name: String, val login: String)
data class AgentProjectDto(
    val id: Int, val name: String, val repository: String, val baseBranch: String,
    val allowPush: Boolean, val allowPr: Boolean, val allowNetwork: Boolean,
    val sandboxMemoryMb: Int, val sandboxCpuPercent: Int,
)
data class AgentTaskDto(
    val id: String, val prompt: String, val ownerId: String, val projectId: Int?, val projectName: String,
    val repository: String, val baseBranch: String, val status: String, val result: String, val error: String,
    val branch: String, val commitSha: String, val pushed: Boolean, val prUrl: String,
    val cancelRequested: Boolean, val parentTaskId: String,
    val createdAt: String, val updatedAt: String,
    val changedFiles: List<String>, val events: List<AgentEventDto>,
)
data class AgentSandboxUsageDto(
    val usedBytes: Long,
    val cacheBytes: Long,
    val workspaceBytes: Long,
    val limitBytes: Long,
    val usedMb: Double,
    val cacheMb: Double,
    val limitMb: Int,
    val percent: Double,
    val overLimit: Boolean,
)

sealed interface AgentApiResult<out T> {
    data class Success<T>(val value: T) : AgentApiResult<T>
    data class Failure(val message: String) : AgentApiResult<Nothing>
}

private data class AgentHttpResponse(val code: Int, val body: String) {
    val successful: Boolean get() = code in 200..299
}

object AgentApi {
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS).writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.MINUTES).callTimeout(0, TimeUnit.SECONDS).build()

    suspend fun saveGitHubConnection(context: Context, token: String, name: String = "GitHub"): AgentApiResult<AgentGitHubConnectionDto> {
        return try {
            val payload = JSONObject().put("name", name).put("token", token)
            val response = executeAuthenticated(context, "POST", "/api/agent/github/connections", payload)
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "GitHub 连接 HTTP ${response.code}"))
            val json = JSONObject(response.body)
            AgentApiResult.Success(AgentGitHubConnectionDto(json.optInt("id"), json.optString("name", "GitHub"), json.optString("login")))
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法保存 GitHub 连接")
        }
    }

    suspend fun saveProject(
        context: Context, connectionId: Int, repository: String, name: String, baseBranch: String = "main",
        allowPush: Boolean = true, allowPr: Boolean = true, allowNetwork: Boolean = false,
        sandboxMemoryMb: Int = 2048, sandboxCpuPercent: Int = 150,
    ): AgentApiResult<AgentProjectDto> {
        return try {
            val payload = JSONObject().put("connection_id", connectionId).put("repo_full_name", repository).put("name", name)
                .put("base_branch", baseBranch).put("allow_push", allowPush).put("allow_pr", allowPr)
                .put("allow_network", allowNetwork).put("sandbox_memory_mb", sandboxMemoryMb).put("sandbox_cpu_percent", sandboxCpuPercent)
            val response = executeAuthenticated(context, "POST", "/api/agent/projects", payload)
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 项目保存 HTTP ${response.code}"))
            AgentApiResult.Success(parseProject(JSONObject(response.body)))
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法保存 Agent 项目")
        }
    }

    suspend fun listProjects(context: Context): AgentApiResult<List<AgentProjectDto>> {
        return try {
            val response = executeAuthenticated(context, "GET", "/api/agent/projects")
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 项目列表 HTTP ${response.code}"))
            val array = JSONObject(response.body).optJSONArray("projects")
            AgentApiResult.Success(buildList { if (array != null) for (index in 0 until array.length()) array.optJSONObject(index)?.let { add(parseProject(it)) } })
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法读取 Agent 项目")
        }
    }

    suspend fun createTask(context: Context, prompt: String, projectId: Int?): AgentApiResult<AgentTaskDto> {
        val payload = JSONObject().put("prompt", prompt)
        if (projectId != null) payload.put("project_id", projectId)
        return taskRequest(context, "POST", "/api/agent/tasks", payload)
    }

    suspend fun getTask(context: Context, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest(context, "GET", "/api/agent/tasks/$taskId")

    suspend fun runTask(context: Context, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest(context, "POST", "/api/agent/tasks/$taskId/run", JSONObject())

    suspend fun cancelTask(context: Context, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest(context, "POST", "/api/agent/tasks/$taskId/cancel", JSONObject())

    suspend fun retryTask(context: Context, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest(context, "POST", "/api/agent/tasks/$taskId/retry", JSONObject())

    suspend fun listTasks(context: Context, status: String = "", limit: Int = 30): AgentApiResult<List<AgentTaskDto>> {
        return try {
            val query = buildString {
                append("/api/agent/tasks?limit=${limit.coerceIn(1, 100)}")
                if (status.isNotBlank()) append("&status=${status.trim()}")
            }
            val response = executeAuthenticated(context, "GET", query)
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 任务历史 HTTP ${response.code}"))
            val array = JSONObject(response.body).optJSONArray("tasks")
            AgentApiResult.Success(buildList {
                if (array != null) for (index in 0 until array.length()) {
                    array.optJSONObject(index)?.let { add(parseTask(it)) }
                }
            })
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法读取 Coding Agent 任务历史")
        }
    }

    suspend fun sandboxUsage(context: Context): AgentApiResult<AgentSandboxUsageDto> {
        return try {
            val response = executeAuthenticated(context, "GET", "/api/agent/sandbox/usage")
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 沙箱状态 HTTP ${response.code}"))
            AgentApiResult.Success(parseSandboxUsage(JSONObject(response.body)))
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法读取 Coding Agent 沙箱空间")
        }
    }

    suspend fun cleanupSandbox(context: Context): AgentApiResult<AgentSandboxUsageDto> {
        return try {
            val response = executeAuthenticated(context, "POST", "/api/agent/sandbox/cleanup", JSONObject())
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 沙箱清理 HTTP ${response.code}"))
            val after = JSONObject(response.body).optJSONObject("after") ?: JSONObject()
            AgentApiResult.Success(parseSandboxUsage(after))
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法清理 Coding Agent 沙箱")
        }
    }

    private suspend fun executeAuthenticated(
        context: Context,
        method: String,
        path: String,
        payload: JSONObject? = null,
    ): AgentHttpResponse = withContext(Dispatchers.IO) {
        val initialToken = CentralSessionManager.ensureAccess(context).orEmpty()
        if (initialToken.isBlank()) return@withContext AgentHttpResponse(401, "{\"detail\":\"FDEX 登录状态已失效，请重新登录\"}")
        var response = executeOnce(method, path, initialToken, payload)
        if (response.code == 401) {
            val refreshed = CentralSessionManager.refreshAfterUnauthorized(context, initialToken).orEmpty()
            if (refreshed.isNotBlank() && refreshed != initialToken) {
                response = executeOnce(method, path, refreshed, payload)
            }
        }
        response
    }

    private fun executeOnce(method: String, path: String, accessToken: String, payload: JSONObject?): AgentHttpResponse {
        client.newCall(authRequest(method, path, accessToken, payload)).execute().use { response ->
            return AgentHttpResponse(response.code, response.body?.string().orEmpty())
        }
    }

    private fun authRequest(method: String, path: String, accessToken: String, payload: JSONObject? = null): Request {
        val builder = Request.Builder().url("${BuildConfig.SERVER_BASE_URL}$path").header("Accept", "application/json")
            .header("Authorization", "Bearer ${accessToken.trim()}")
        if (method == "GET") builder.get() else builder.post((payload ?: JSONObject()).toString().toRequestBody(jsonMediaType))
        return builder.build()
    }

    private suspend fun taskRequest(context: Context, method: String, path: String, payload: JSONObject? = null): AgentApiResult<AgentTaskDto> {
        return try {
            val response = executeAuthenticated(context, method, path, payload)
            if (!response.successful) return AgentApiResult.Failure(extractError(response.body, "Agent 服务返回 HTTP ${response.code}"))
            AgentApiResult.Success(parseTask(JSONObject(response.body)))
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法连接 FDEX Agent 服务")
        }
    }

    private fun parseProject(item: JSONObject): AgentProjectDto = AgentProjectDto(
        id = item.optInt("id"), name = item.optString("name"), repository = item.optString("repository"),
        baseBranch = item.optString("base_branch", "main"), allowPush = item.optBoolean("allow_push"), allowPr = item.optBoolean("allow_pr"),
        allowNetwork = item.optBoolean("allow_network"), sandboxMemoryMb = item.optInt("sandbox_memory_mb", 2048), sandboxCpuPercent = item.optInt("sandbox_cpu_percent", 150),
    )

    private fun parseSandboxUsage(json: JSONObject): AgentSandboxUsageDto = AgentSandboxUsageDto(
        usedBytes = json.optLong("used_bytes"),
        cacheBytes = json.optLong("cache_bytes"),
        workspaceBytes = json.optLong("workspace_bytes"),
        limitBytes = json.optLong("limit_bytes"),
        usedMb = json.optDouble("used_mb"),
        cacheMb = json.optDouble("cache_mb"),
        limitMb = json.optInt("limit_mb"),
        percent = json.optDouble("percent"),
        overLimit = json.optBoolean("over_limit"),
    )

    internal fun parseTask(json: JSONObject): AgentTaskDto {
        val changed = json.optJSONArray("changed_files")
        val files = buildList { if (changed != null) for (index in 0 until changed.length()) changed.optString(index).takeIf { it.isNotBlank() }?.let(::add) }
        val eventsJson = json.optJSONArray("events")
        val events = buildList { if (eventsJson != null) for (index in 0 until eventsJson.length()) {
            val item = eventsJson.optJSONObject(index) ?: continue
            add(AgentEventDto(item.optString("type"), item.optString("message"), item.optString("created_at")))
        } }
        return AgentTaskDto(
            id = json.optString("id"), prompt = json.optString("prompt"), ownerId = json.optString("owner_id"),
            projectId = json.optInt("project_id", 0).takeIf { it > 0 }, projectName = json.optString("project_name", "Local FDEX"),
            repository = json.optString("repository"), baseBranch = json.optString("base_branch", "main"), status = json.optString("status"),
            result = json.optString("result"), error = json.optString("error"), branch = json.optString("branch"), commitSha = json.optString("commit_sha"),
            pushed = json.optBoolean("pushed"), prUrl = json.optString("pr_url"),
            cancelRequested = json.optBoolean("cancel_requested"), parentTaskId = json.optString("parent_task_id"),
            createdAt = json.optString("created_at"), updatedAt = json.optString("updated_at"),
            changedFiles = files, events = events,
        )
    }

    private fun extractError(body: String, fallback: String): String = runCatching {
        val detail = JSONObject(body).opt("detail")
        when (detail) { is String -> detail; is JSONObject -> detail.optString("message").ifBlank { detail.toString() }; null -> ""; else -> detail.toString() }
    }.getOrNull().orEmpty().ifBlank { fallback }
}
