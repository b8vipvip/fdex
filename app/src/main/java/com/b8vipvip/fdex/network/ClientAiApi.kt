package com.b8vipvip.fdex.network

import android.content.Context
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.CentralSessionStore
import com.b8vipvip.fdex.diagnostics.ClientRuntimeLog
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
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

internal sealed interface SseLineResult {
    data class Event(val event: AiStreamEvent) : SseLineResult
    data object DoneMarker : SseLineResult
}

object ClientAiApi {
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()
    internal const val ATTACHMENT_STREAM_READ_TIMEOUT_SECONDS = 120L

    private val requestClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(90, TimeUnit.SECONDS)
        .readTimeout(420, TimeUnit.SECONDS)
        .callTimeout(0, TimeUnit.SECONDS)
        .build()

    private val streamClient = requestClient.newBuilder()
        .readTimeout(90, TimeUnit.SECONDS)
        .build()

    private val attachmentStreamClient = requestClient.newBuilder()
        .readTimeout(ATTACHMENT_STREAM_READ_TIMEOUT_SECONDS, TimeUnit.SECONDS)
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
            openAuthenticatedResponse(requestClient, context) { accessToken, userId ->
                buildRequest(
                    path = "/api/client/ai",
                    accept = "application/json",
                    payload = payload,
                    requestId = requestId,
                    mode = "fallback",
                    accessToken = accessToken,
                    userId = userId,
                )
            }.use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    logFailure("http_error", requestId, "fallback", mapOf("http_code" to response.code))
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
            logFailure(
                "exception",
                requestId,
                "fallback",
                mapOf("error_type" to error::class.java.simpleName, "error" to error.message.orEmpty()),
            )
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
            val parsedChat = parseChatContent(prompt)
            val attachments = parsedChat.attachments
            if (attachments.isNotEmpty()) emit(AiStreamEvent.Status("正在读取附件并生成请求数据… 请求 ${requestId.take(8)}"))
            val payload = buildPayload(system, prompt, maxTokens, context)
            if (attachments.isNotEmpty()) emit(AiStreamEvent.Status("附件已准备，正在上传到 FDEX 服务端… 请求 ${requestId.take(8)}"))
            val hasAudioAttachment = attachments.any {
                it.kind == ChatAttachmentKind.AUDIO || it.mimeType.startsWith("audio/", ignoreCase = true)
            }
            val client = if (attachments.isNotEmpty() && !hasAudioAttachment) attachmentStreamClient else streamClient

            openAuthenticatedResponse(client, context) { accessToken, userId ->
                buildRequest(
                    path = "/api/client/ai/stream",
                    accept = "text/event-stream",
                    payload = payload,
                    requestId = requestId,
                    mode = "stream",
                    accessToken = accessToken,
                    userId = userId,
                )
            }.use { response ->
                if (!response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    logFailure("http_error", requestId, "stream", mapOf("http_code" to response.code))
                    emit(AiStreamEvent.Failure(withRequestId(extractError(body, "服务端返回 HTTP ${response.code}"), requestId)))
                    return@flow
                }
                val responseBody = response.body
                if (responseBody == null) {
                    logFailure("empty_body", requestId, "stream")
                    emit(AiStreamEvent.Failure(withRequestId("服务端没有返回流式响应正文", requestId)))
                    return@flow
                }
                val source = responseBody.source()
                var resultSeen = false
                while (true) {
                    val line = source.readUtf8Line() ?: break
                    when (val parsed = parseSseLine(line)) {
                        null -> Unit
                        SseLineResult.DoneMarker -> {
                            if (resultSeen) emit(AiStreamEvent.Done(model = "", latencyMs = 0))
                            else {
                                logFailure("done_without_result", requestId, "stream")
                                emit(AiStreamEvent.Failure(withRequestId("流式连接已结束，但没有收到正文或媒体结果", requestId)))
                            }
                            return@flow
                        }
                        is SseLineResult.Event -> {
                            val event = parsed.event
                            if (event is AiStreamEvent.Content || event is AiStreamEvent.Media) {
                                if (!resultSeen) emit(AiStreamEvent.Status(""))
                                resultSeen = true
                            }
                            if (event is AiStreamEvent.Failure) {
                                logFailure("server_error_event", requestId, "stream", mapOf("message" to event.message))
                            }
                            emit(event)
                            if (event is AiStreamEvent.Done || event is AiStreamEvent.Failure) return@flow
                        }
                    }
                }
                if (resultSeen) emit(AiStreamEvent.Done(model = "", latencyMs = 0))
                else {
                    logFailure("stream_ended_early", requestId, "stream")
                    emit(AiStreamEvent.Failure(withRequestId("流式连接提前结束，未收到正文或媒体结果", requestId)))
                }
            }
        } catch (error: Exception) {
            logFailure(
                "exception",
                requestId,
                "stream",
                mapOf("error_type" to error::class.java.simpleName, "error" to error.message.orEmpty()),
            )
            emit(AiStreamEvent.Failure(withRequestId(error.message ?: "AI 流式连接失败", requestId)))
        }
    }.flowOn(Dispatchers.IO)

    internal fun extractSseData(line: String): String? {
        val normalized = line.trimStart()
        if (!normalized.startsWith("data:")) return null
        return normalized.removePrefix("data:").trim().takeIf { it.isNotBlank() }
    }

    internal fun parseSseLine(line: String): SseLineResult? {
        val raw = extractSseData(line) ?: return null
        if (raw == "[DONE]") return SseLineResult.DoneMarker
        return parseStreamData(raw)?.let(SseLineResult::Event)
    }

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
            for (index in 0 until array.length()) array.optJSONObject(index)?.let(::parseMedia)?.let(::add)
        }
    }

    private suspend fun openAuthenticatedResponse(
        client: OkHttpClient,
        context: Context?,
        requestFactory: (accessToken: String, userId: String) -> Request,
    ): Response {
        val store = context?.let(::CentralSessionStore)
        val initialToken = if (context != null) CentralSessionManager.ensureAccess(context).orEmpty() else ""
        val userId = store?.userId().orEmpty()
        var response = client.newCall(requestFactory(initialToken, userId)).execute()
        if (response.code == 401 && context != null && initialToken.isNotBlank()) {
            val refreshed = CentralSessionManager.refreshAfterUnauthorized(context, initialToken).orEmpty()
            if (refreshed.isNotBlank() && refreshed != initialToken) {
                response.close()
                response = client.newCall(requestFactory(refreshed, CentralSessionStore(context).userId())).execute()
            }
        }
        return response
    }

    private fun buildRequest(
        path: String,
        accept: String,
        payload: JSONObject,
        requestId: String,
        mode: String,
        accessToken: String,
        userId: String,
    ): Request {
        val bytes = payload.toString().toByteArray(Charsets.UTF_8)
        val builder = Request.Builder()
            .url("${BuildConfig.SERVER_BASE_URL}$path")
            .post(bytes.toRequestBody(jsonMediaType))
            .header("Accept", accept)
            .header("Cache-Control", "no-cache")
            .header("Accept-Encoding", "identity")
            .header("X-FDEX-Request-ID", requestId)
            .header("X-FDEX-Request-Mode", mode)
            .header("X-FDEX-Payload-Bytes", bytes.size.toString())
        if (accessToken.isNotBlank()) {
            builder.header("Authorization", "Bearer ${accessToken.trim()}")
            if (userId.isNotBlank()) builder.header("X-FDEX-User-ID", userId)
        }
        return builder.build()
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
            payload.put("images", JSONArray().apply {
                prepared.images.forEach { image -> put(JSONObject().put("url", image.url).put("detail", image.detail)) }
            })
        }
        prepared.audio?.let { audio -> payload.put("audio", JSONObject().put("data", audio.data).put("format", audio.format)) }
        if (prepared.documents.isNotEmpty()) {
            payload.put("documents", JSONArray().apply {
                prepared.documents.forEach { document ->
                    put(JSONObject().put("name", document.name).put("mime_type", document.mimeType).put("data", document.data))
                }
            })
        }
        return payload
    }

    private fun logFailure(event: String, requestId: String, mode: String, details: Map<String, Any?> = emptyMap()) {
        ClientRuntimeLog.warn(
            component = "client_ai",
            event = event,
            message = "FDEX AI request failed",
            details = mapOf("request_id" to requestId.take(16), "mode" to mode) + details,
        )
    }

    private fun extractError(body: String, fallback: String): String =
        runCatching {
            val detail = JSONObject(body).opt("detail")
            when (detail) { is String -> detail; null -> ""; else -> detail.toString() }
        }.getOrNull().orEmpty().ifBlank { fallback }

    private fun withRequestId(message: String, requestId: String): String {
        if (message.contains("请求ID")) return message
        return "$message（请求ID：${requestId.take(12)}）"
    }
}
