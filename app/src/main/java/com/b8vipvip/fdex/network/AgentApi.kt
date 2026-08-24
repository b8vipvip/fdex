package com.b8vipvip.fdex.network

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

data class AgentProjectDto(
    val id: Int,
    val name: String,
    val repository: String,
    val baseBranch: String,
    val allowPush: Boolean,
    val allowPr: Boolean,
)

data class AgentTaskDto(
    val id: String,
    val prompt: String,
    val ownerId: String,
    val projectId: Int?,
    val projectName: String,
    val repository: String,
    val baseBranch: String,
    val status: String,
    val result: String,
    val error: String,
    val branch: String,
    val commitSha: String,
    val pushed: Boolean,
    val prUrl: String,
    val changedFiles: List<String>,
    val events: List<AgentEventDto>,
)

sealed interface AgentApiResult<out T> {
    data class Success<T>(val value: T) : AgentApiResult<T>
    data class Failure(val message: String) : AgentApiResult<Nothing>
}

object AgentApi {
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.MINUTES)
        .callTimeout(0, TimeUnit.SECONDS)
        .build()

    suspend fun listProjects(token: String): AgentApiResult<List<AgentProjectDto>> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("${BuildConfig.SERVER_BASE_URL}/api/agent/projects")
                .header("Accept", "application/json")
                .header("X-FDEX-Agent-Token", token.trim())
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) return@withContext AgentApiResult.Failure(extractError(body, "Agent 项目列表 HTTP ${response.code}"))
                val array = JSONObject(body).optJSONArray("projects")
                val items = buildList {
                    if (array != null) for (index in 0 until array.length()) {
                        val item = array.optJSONObject(index) ?: continue
                        add(
                            AgentProjectDto(
                                id = item.optInt("id"),
                                name = item.optString("name"),
                                repository = item.optString("repository"),
                                baseBranch = item.optString("base_branch", "main"),
                                allowPush = item.optBoolean("allow_push"),
                                allowPr = item.optBoolean("allow_pr"),
                            ),
                        )
                    }
                }
                AgentApiResult.Success(items)
            }
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法读取 Agent 项目")
        }
    }

    suspend fun createTask(token: String, prompt: String, projectId: Int?): AgentApiResult<AgentTaskDto> {
        val payload = JSONObject().put("prompt", prompt)
        if (projectId != null) payload.put("project_id", projectId)
        return taskRequest("POST", "/api/agent/tasks", token, payload)
    }

    suspend fun getTask(token: String, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest("GET", "/api/agent/tasks/$taskId", token)

    suspend fun runTask(token: String, taskId: String): AgentApiResult<AgentTaskDto> =
        taskRequest("POST", "/api/agent/tasks/$taskId/run", token, JSONObject())

    private suspend fun taskRequest(method: String, path: String, token: String, payload: JSONObject? = null): AgentApiResult<AgentTaskDto> = withContext(Dispatchers.IO) {
        try {
            val builder = Request.Builder()
                .url("${BuildConfig.SERVER_BASE_URL}$path")
                .header("Accept", "application/json")
                .header("X-FDEX-Agent-Token", token.trim())
            when (method) {
                "GET" -> builder.get()
                else -> builder.post((payload ?: JSONObject()).toString().toRequestBody(jsonMediaType))
            }
            client.newCall(builder.build()).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) return@withContext AgentApiResult.Failure(extractError(body, "Agent 服务返回 HTTP ${response.code}"))
                AgentApiResult.Success(parseTask(JSONObject(body)))
            }
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法连接 FDEX Agent 服务")
        }
    }

    internal fun parseTask(json: JSONObject): AgentTaskDto {
        val changed = json.optJSONArray("changed_files")
        val files = buildList {
            if (changed != null) for (index in 0 until changed.length()) changed.optString(index).takeIf { it.isNotBlank() }?.let(::add)
        }
        val eventsJson = json.optJSONArray("events")
        val events = buildList {
            if (eventsJson != null) for (index in 0 until eventsJson.length()) {
                val item = eventsJson.optJSONObject(index) ?: continue
                add(AgentEventDto(item.optString("type"), item.optString("message"), item.optString("created_at")))
            }
        }
        val projectId = json.optInt("project_id", 0).takeIf { it > 0 }
        return AgentTaskDto(
            id = json.optString("id"), prompt = json.optString("prompt"), ownerId = json.optString("owner_id", "local"),
            projectId = projectId, projectName = json.optString("project_name", "Local FDEX"), repository = json.optString("repository"),
            baseBranch = json.optString("base_branch", "main"), status = json.optString("status"), result = json.optString("result"), error = json.optString("error"),
            branch = json.optString("branch"), commitSha = json.optString("commit_sha"), pushed = json.optBoolean("pushed"), prUrl = json.optString("pr_url"),
            changedFiles = files, events = events,
        )
    }

    private fun extractError(body: String, fallback: String): String = runCatching {
        val detail = JSONObject(body).opt("detail")
        when (detail) { is String -> detail; null -> ""; else -> detail.toString() }
    }.getOrNull().orEmpty().ifBlank { fallback }
}
