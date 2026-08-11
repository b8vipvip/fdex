package com.b8vipvip.fdex.network

import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

sealed interface AiGatewayResult {
    data class Success(val content: String, val model: String, val latencyMs: Int) : AiGatewayResult
    data class Failure(val message: String) : AiGatewayResult
}

object ClientAiApi {
    suspend fun ask(system: String?, prompt: String, maxTokens: Int = 1200): AiGatewayResult = withContext(Dispatchers.IO) {
        val connection = runCatching {
            URL("${BuildConfig.SERVER_BASE_URL}/api/client/ai").openConnection() as HttpURLConnection
        }.getOrElse { return@withContext AiGatewayResult.Failure("服务地址无效") }

        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 90_000
            connection.doOutput = true
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val payload = JSONObject()
                .put("prompt", prompt)
                .put("max_tokens", maxTokens.coerceIn(32, 4000))
            if (!system.isNullOrBlank()) payload.put("system", system)
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }

            val code = connection.responseCode
            val body = (if (code in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                val message = runCatching { JSONObject(body).optString("detail") }.getOrNull().orEmpty()
                return@withContext AiGatewayResult.Failure(message.ifBlank { "服务端返回 HTTP $code" })
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
}