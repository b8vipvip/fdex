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


data class AgentEventDto(
    val type: String,
    val message: String,
    val createdAt: String,
)

data class AgentTaskDto(
    val id: String,
    val prompt: String,
    val status: String,
    val result: String,
    val error: String,
    val branch: String,
    val commitSha: String,
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

    suspend fun createTask(token: String, prompt: String): AgentApiResult<AgentTaskDto> = request(
        method = "POST",
        path = "/api/agent/tasks",
        token = token,
        payload = JSONObject().put("prompt", prompt),
    )

    suspend fun getTask(token: String, taskId: String): AgentApiResult<AgentTaskDto> = request(
        method = "GET",
        path = "/api/agent/tasks/$taskId",
        token = token,
    )

    suspend fun runTask(token: String, taskId: String): AgentApiResult<AgentTaskDto> = request(
        method = "POST",
        path = "/api/agent/tasks/$taskId/run",
        token = token,
        payload = JSONObject(),
    )

    private suspend fun request(
        method: String,
        path: String,
        token: String,
        payload: JSONObject? = null,
    ): AgentApiResult<AgentTaskDto> = withContext(Dispatchers.IO) {
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
                if (!response.isSuccessful) {
                    return@withContext AgentApiResult.Failure(extractError(body, "Agent 服务返回 HTTP ${response.code}"))
                }
                AgentApiResult.Success(parseTask(JSONObject(body)))
            }
        } catch (error: Exception) {
            AgentApiResult.Failure(error.message ?: "无法连接 FDEX Agent 服务")
        }
    }

    internal fun parseTask(json: JSONObject): AgentTaskDto {
        val changed = json.optJSONArray("changed_files")
        val files = buildList {
            if (changed != null) for (index in 0 until changed.length()) {
                changed.optString(index).takeIf { it.isNotBlank() }?.let(::add)
            }
        }
        val eventsJson = json.optJSONArray("events")
        val events = buildList {
            if (eventsJson != null) for (index in 0 until eventsJson.length()) {
                val item = eventsJson.optJSONObject(index) ?: continue
                add(
                    AgentEventDto(
                        type = item.optString("type"),
                        message = item.optString("message"),
                        createdAt = item.optString("created_at"),
                    ),
                )
            }
        }
        return AgentTaskDto(
            id = json.optString("id"),
            prompt = json.optString("prompt"),
            status = json.optString("status"),
            result = json.optString("result"),
            error = json.optString("error"),
            branch = json.optString("branch"),
            commitSha = json.optString("commit_sha"),
            changedFiles = files,
            events = events,
        )
    }

    private fun extractError(body: String, fallback: String): String = runCatching {
        val detail = JSONObject(body).opt("detail")
        when (detail) {
            is String -> detail
            null -> ""
            else -> detail.toString()
        }
    }.getOrNull().orEmpty().ifBlank { fallback }
}
