package com.b8vipvip.fdex.network

import android.content.Context
import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.TimeUnit

sealed interface AiGatewayResult {
    data class Success(
        val content: String,
        val model: String,
        val latencyMs: Int,
        val media: List<AiMediaResult> = emptyList(),
    ) : AiGatewayResult
    data class Failure(val message: String) : AiGatewayResult
}

sealed interface AiStreamEvent {
    data class Status(val status: String) : AiStreamEvent
    data class Reasoning(val delta: String) : AiStreamEvent
    data class Content(val delta: String) : AiStreamEvent
    data class Media(val media: AiMediaResult) : AiStreamEvent
    data class Done(val model: String, val latencyMs: Int) : AiStreamEvent
    data class Failure(val message: String) : AiStreamEvent
}

object ClientAiApi {
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    // Attachment requests used HttpURLConnection before v1.1.16. It has no practical write timeout,
    // so a large Base64 request could stay on "preparing attachment" for several minutes. OkHttp
    // gives the upload, connect and SSE idle phases independent limits.
    private val requestClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(90, TimeUnit.SECONDS)
        .readTimeout(420, TimeUnit.SECONDS)
        .callTimeout(0, TimeUnit.SECONDS)
        .build()

    private val streamClient = requestClient.newBuilder()
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    fun newRequestId(): String = UUID.randomUUID().toString()

    suspend fun ask(
        system: String?,
        prompt: String,
        maxTokens: Int = 1200,
        context: Context? = null,
        requestId: String = newRequestId(),
    ): AiGatewayResult = withContext(Dispatchers.IO) {
        try {
            val payload = buildPayload(system, prompt, maxTokens, context)
            val request = buildRequest(
                path = "/api/client/ai",
                accept = "application/json",
                payload = payload,
                requestId = requestId,
                mode = "fallback",
            )
            requestClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return@withContext AiGatewayResult.Failure(
                        withRequestId(extractError(body, "服务端返回 HTTP ${response.code}"), requestId),
                    )
                }
                val json = JSONObject(body)
                AiGatewayResult.Success(
                    content = json.optString("content"),
                    model = json.optString("model"),
                    latencyMs = json.optInt("latency_ms"),
                    media = parseMediaArray(json.optJSONArray("media")),
                )
            }
        } catch (error: Exception) {
            AiGatewayResult.Failure(withRequestId(error.message ?: "无法连接 FDEX 服务端", requestId))
        }
    }

    fun streamAsk(
        system: String?,
        prompt: String,
        maxTokens: Int = 1200,
        context: Context? = null,
        requestId: String = newRequestId(),
    ): Flow<AiStreamEvent> = flow {
        try {
            val attachmentCount = parseChatContent(prompt).attachments.size
            if (attachmentCount > 0) {
                emit(AiStreamEvent.Status("正在读取附件并生成请求数据… 请求 ${requestId.take(8)}"))
            }
            val payload = buildPayload(system, prompt, maxTokens, context)
            if (attachmentCount > 0) {
                emit(AiStreamEvent.Status("附件已准备，正在上传到 FDEX 服务端… 请求 ${requestId.take(8)}"))
            }
            val request = buildRequest(
                path = "/api/client/ai/stream",
                accept = "text/event-stream",
                payload = payload,
                requestId = requestId,
                mode = "stream",
            )

            streamClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    emit(
                        AiStreamEvent.Failure(
                            withRequestId(extractError(body, "服务端返回 HTTP ${response.code}"), requestId),
                        ),
                    )
                    return@flow
                }

                val responseBody = response.body
                if (responseBody == null) {
                    emit(AiStreamEvent.Failure(withRequestId("服务端没有返回流式响应正文", requestId)))
                    return@flow
                }
                responseBody.charStream().buffered().use { reader ->
                    for (line in reader.lineSequence()) {
                        if (!line.startsWith("data:")) continue
                        val raw = line.removePrefix("data:").trim()
                        if (raw.isBlank()) continue
                        if (raw == "[DONE]") break
                        parseStreamData(raw)?.let { event -> emit(event) }
                    }
                }
            }
        } catch (error: Exception) {
            emit(AiStreamEvent.Failure(withRequestId(error.message ?: "AI 流式连接失败", requestId)))
        }
    }.flowOn(Dispatchers.IO)

    internal fun parseStreamData(raw: String): AiStreamEvent? {
        val json = runCatching { JSONObject(raw) }.getOrNull() ?: return null
        return when (json.optString("type")) {
            "status" -> json.optString("status").takeIf { it.isNotBlank() }?.let(AiStreamEvent::Status)
            "reasoning" -> json.optString("delta").takeIf { it.isNotEmpty() }?.let(AiStreamEvent::Reasoning)
            "content" -> json.optString("delta").takeIf { it.isNotEmpty() }?.let(AiStreamEvent::Content)
            "media" -> parseMedia(json)?.let(AiStreamEvent::Media)
            "done" -> AiStreamEvent.Done(json.optString("model"), json.optInt("latency_ms"))
            "error" -> AiStreamEvent.Failure(json.optString("message").ifBlank { "AI 流式请求失败" })
            else -> null
        }
    }

    private fun parseMedia(json: JSONObject): AiMediaResult? {
        val url = json.optString("url")
        if (url.isBlank()) return null
        return AiMediaResult(
            kind = json.optString("kind"),
            url = url,
            mimeType = json.optString("mime_type"),
            transcript = json.optString("transcript"),
            revisedPrompt = json.optString("revised_prompt"),
        )
    }

    private fun parseMediaArray(array: JSONArray?): List<AiMediaResult> {
        if (array == null) return emptyList()
        return buildList {
            for (index in 0 until array.length()) {
                array.optJSONObject(index)?.let(::parseMedia)?.let(::add)
            }
        }
    }

    private fun buildRequest(
        path: String,
        accept: String,
        payload: JSONObject,
        requestId: String,
        mode: String,
    ): Request {
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        return Request.Builder()
            .url("${BuildConfig.SERVER_BASE_URL}$path")
            .post(bytes.toRequestBody(jsonMediaType))
            .header("Accept", accept)
            .header("Cache-Control", "no-cache")
            .header("Accept-Encoding", "identity")
            .header("X-FDEX-Request-ID", requestId)
            .header("X-FDEX-Request-Mode", mode)
            .header("X-FDEX-Payload-Bytes", bytes.size.toString())
            .build()
    }

    private fun buildPayload(
        system: String?,
        prompt: String,
        maxTokens: Int,
        context: Context?,
    ): JSONObject {
        val prepared = prepareAiContent(context, prompt)
        val payload = JSONObject()
            .put("prompt", prepared.prompt)
            .put("max_tokens", maxTokens.coerceIn(32, 4000))

        if (!system.isNullOrBlank()) payload.put("system", system)
        if (prepared.images.isNotEmpty()) {
            payload.put(
                "images",
                JSONArray().apply {
                    prepared.images.forEach { image ->
                        put(JSONObject().put("url", image.url).put("detail", image.detail))
                    }
                },
            )
        }
        prepared.audio?.let { audio ->
            payload.put("audio", JSONObject().put("data", audio.data).put("format", audio.format))
        }
        if (prepared.documents.isNotEmpty()) {
            payload.put(
                "documents",
                JSONArray().apply {
                    prepared.documents.forEach { document ->
                        put(
                            JSONObject()
                                .put("name", document.name)
                                .put("mime_type", document.mimeType)
                                .put("data", document.data),
                        )
                    }
                },
            )
        }
        return payload
    }

    private fun extractError(body: String, fallback: String): String =
        runCatching {
            val detail = JSONObject(body).opt("detail")
            when (detail) {
                is String -> detail
                null -> ""
                else -> detail.toString()
            }
        }.getOrNull().orEmpty().ifBlank { fallback }

    private fun withRequestId(message: String, requestId: String): String {
        if (message.contains("请求ID")) return message
        return "$message（请求ID：${requestId.take(12)}）"
    }
}
