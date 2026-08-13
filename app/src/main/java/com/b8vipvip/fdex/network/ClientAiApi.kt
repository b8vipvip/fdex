package com.b8vipvip.fdex.network

import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

sealed interface AiGatewayResult {
    data class Success(val content: String, val model: String, val latencyMs: Int) : AiGatewayResult
    data class Failure(val message: String) : AiGatewayResult
}

sealed interface AiStreamEvent {
    data class Status(val status: String) : AiStreamEvent
    data class Reasoning(val delta: String) : AiStreamEvent
    data class Content(val delta: String) : AiStreamEvent
    data class Done(val model: String, val latencyMs: Int) : AiStreamEvent
    data class Failure(val message: String) : AiStreamEvent
}

object ClientAiApi {
    suspend fun ask(system: String?, prompt: String, maxTokens: Int = 1200): AiGatewayResult = withContext(Dispatchers.IO) {
        val connection = open("/api/client/ai") ?: return@withContext AiGatewayResult.Failure("服务地址无效")
        try {
            configure(connection, accept = "application/json")
            writePayload(connection, system, prompt, maxTokens)

            val code = connection.responseCode
            val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                return@withContext AiGatewayResult.Failure(extractError(body, "服务端返回 HTTP $code"))
            }
            val json = JSONObject(body)
            AiGatewayResult.Success(
                content = json.optString("content"),
                model = json.optString("model"),
                latencyMs = json.optInt("latency_ms"),
            )
        } catch (error: Exception) {
            AiGatewayResult.Failure(error.message ?: "无法连接 FDEX 服务端")
        } finally {
            connection.disconnect()
        }
    }

    fun streamAsk(system: String?, prompt: String, maxTokens: Int = 1200): Flow<AiStreamEvent> = flow {
        val connection = open("/api/client/ai/stream")
        if (connection == null) {
            emit(AiStreamEvent.Failure("服务地址无效"))
            return@flow
        }

        try {
            configure(connection, accept = "text/event-stream")
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-cache")
            connection.setRequestProperty("Accept-Encoding", "identity")
            writePayload(connection, system, prompt, maxTokens)

            val code = connection.responseCode
            if (code !in 200..299) {
                val body = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                emit(AiStreamEvent.Failure(extractError(body, "服务端返回 HTTP $code")))
                return@flow
            }

            connection.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                for (line in reader.lineSequence()) {
                    if (!line.startsWith("data:")) continue
                    val raw = line.removePrefix("data:").trim()
                    if (raw.isBlank()) continue
                    if (raw == "[DONE]") break
                    parseStreamData(raw)?.let { event -> emit(event) }
                }
            }
        } catch (error: Exception) {
            emit(AiStreamEvent.Failure(error.message ?: "AI 流式连接失败"))
        } finally {
            connection.disconnect()
        }
    }.flowOn(Dispatchers.IO)

    internal fun parseStreamData(raw: String): AiStreamEvent? {
        val json = runCatching { JSONObject(raw) }.getOrNull() ?: return null
        return when (json.optString("type")) {
            "status" -> json.optString("status").takeIf { it.isNotBlank() }?.let(AiStreamEvent::Status)
            "reasoning" -> json.optString("delta").takeIf { it.isNotEmpty() }?.let(AiStreamEvent::Reasoning)
            "content" -> json.optString("delta").takeIf { it.isNotEmpty() }?.let(AiStreamEvent::Content)
            "done" -> AiStreamEvent.Done(json.optString("model"), json.optInt("latency_ms"))
            "error" -> AiStreamEvent.Failure(json.optString("message").ifBlank { "AI 流式请求失败" })
            else -> null
        }
    }

    private fun open(path: String): HttpURLConnection? = runCatching {
        URL("${BuildConfig.SERVER_BASE_URL}$path").openConnection() as HttpURLConnection
    }.getOrNull()

    private fun configure(connection: HttpURLConnection, accept: String) {
        connection.requestMethod = "POST"
        connection.connectTimeout = 15_000
        connection.readTimeout = 120_000
        connection.doOutput = true
        connection.setRequestProperty("Accept", accept)
        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
    }

    private fun writePayload(connection: HttpURLConnection, system: String?, prompt: String, maxTokens: Int) {
        val payload = JSONObject()
            .put("prompt", prompt)
            .put("max_tokens", maxTokens.coerceIn(32, 4000))
        if (!system.isNullOrBlank()) payload.put("system", system)
        connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
    }

    private fun extractError(body: String, fallback: String): String =
        runCatching { JSONObject(body).optString("detail") }.getOrNull().orEmpty().ifBlank { fallback }
}
