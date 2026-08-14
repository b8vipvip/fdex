package com.b8vipvip.fdex.network

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

private const val ATTACHMENT_MARKER = "FDEX_ATTACHMENTS_V1"
private const val MAX_IMAGE_BYTES = 8 * 1024 * 1024
private const val MAX_AUDIO_BYTES = 16 * 1024 * 1024
private const val MAX_IMAGES = 4


enum class ChatAttachmentKind(val wireName: String) {
    IMAGE("image"),
    VIDEO("video"),
    AUDIO("audio"),
    FILE("file");

    companion object {
        fun fromWireName(value: String): ChatAttachmentKind = entries.firstOrNull { it.wireName == value } ?: FILE
    }
}


data class ChatAttachment(
    val name: String,
    val uri: String,
    val mimeType: String,
    val size: Long,
    val kind: ChatAttachmentKind,
)


data class ParsedChatContent(
    val text: String,
    val attachments: List<ChatAttachment>,
)


data class AiImagePayload(
    val url: String,
    val detail: String = "auto",
)


data class AiAudioPayload(
    val data: String,
    val format: String,
)


data class PreparedAiContent(
    val prompt: String,
    val images: List<AiImagePayload> = emptyList(),
    val audio: AiAudioPayload? = null,
)


fun chatAttachmentFromUri(context: Context, uri: Uri, kind: ChatAttachmentKind): ChatAttachment {
    val resolver = context.contentResolver
    var name = uri.lastPathSegment?.substringAfterLast('/')?.takeIf { it.isNotBlank() } ?: "附件"
    var size = -1L
    runCatching {
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (nameIndex >= 0) cursor.getString(nameIndex)?.takeIf { it.isNotBlank() }?.let { name = it }
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = cursor.getLong(sizeIndex)
            }
        }
    }
    val mime = resolver.getType(uri).orEmpty().ifBlank { "application/octet-stream" }
    return ChatAttachment(name = name, uri = uri.toString(), mimeType = mime, size = size, kind = kind)
}


fun persistChatAttachmentPermission(context: Context, uri: Uri) {
    runCatching {
        context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
}


fun encodeChatContent(text: String, attachments: List<ChatAttachment>): String {
    val clean = text.trim()
    if (attachments.isEmpty()) return clean
    val payload = JSONArray().apply {
        attachments.forEach { attachment ->
            put(
                JSONObject()
                    .put("name", attachment.name)
                    .put("uri", attachment.uri)
                    .put("mime", attachment.mimeType)
                    .put("size", attachment.size)
                    .put("kind", attachment.kind.wireName)
            )
        }
    }.toString()
    val encoded = Base64.encodeToString(payload.toByteArray(Charsets.UTF_8), Base64.URL_SAFE or Base64.NO_WRAP)
    return buildString {
        if (clean.isNotBlank()) append(clean).append("\n\n")
        append("[[").append(ATTACHMENT_MARKER).append(':').append(encoded).append("]]" )
    }
}


fun parseChatContent(content: String): ParsedChatContent {
    val marker = Regex("(?s)\\s*\\[\\[$ATTACHMENT_MARKER:([A-Za-z0-9_\\-=]+)]]\\s*$")
    val match = marker.find(content) ?: return ParsedChatContent(content, emptyList())
    val raw = runCatching {
        String(Base64.decode(match.groupValues[1], Base64.URL_SAFE or Base64.NO_WRAP), Charsets.UTF_8)
    }.getOrNull() ?: return ParsedChatContent(content, emptyList())
    val attachments = runCatching {
        val array = JSONArray(raw)
        buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                val uri = item.optString("uri")
                if (uri.isBlank()) continue
                add(
                    ChatAttachment(
                        name = item.optString("name", "附件"),
                        uri = uri,
                        mimeType = item.optString("mime", "application/octet-stream"),
                        size = item.optLong("size", -1L),
                        kind = ChatAttachmentKind.fromWireName(item.optString("kind", "file")),
                    )
                )
            }
        }
    }.getOrDefault(emptyList())
    return ParsedChatContent(content.removeRange(match.range).trim(), attachments)
}


fun prepareAiContent(context: Context?, content: String): PreparedAiContent {
    val parsed = parseChatContent(content)
    if (parsed.attachments.isEmpty()) return PreparedAiContent(parsed.text)

    val images = mutableListOf<AiImagePayload>()
    var audio: AiAudioPayload? = null
    val notes = mutableListOf<String>()

    parsed.attachments.forEach { attachment ->
        val lowerMime = attachment.mimeType.lowercase()
        when {
            attachment.kind == ChatAttachmentKind.IMAGE || lowerMime.startsWith("image/") -> {
                if (images.size >= MAX_IMAGES) {
                    notes += "图片《${attachment.name}》未送入视觉模型：一次最多处理 $MAX_IMAGES 张图片。"
                } else if (context == null) {
                    notes += "图片《${attachment.name}》已附加，但当前调用没有文件读取上下文。"
                } else {
                    val bytes = readAttachmentBytes(context, attachment, MAX_IMAGE_BYTES)
                    if (bytes == null) {
                        notes += "图片《${attachment.name}》未送入视觉模型：文件不可读取或超过 8 MB。"
                    } else {
                        val mime = lowerMime.takeIf { it.startsWith("image/") } ?: "image/jpeg"
                        val base64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
                        images += AiImagePayload("data:$mime;base64,$base64")
                    }
                }
            }

            attachment.kind == ChatAttachmentKind.AUDIO || lowerMime.startsWith("audio/") -> {
                if (audio != null) {
                    notes += "语音《${attachment.name}》作为附加文件保留；一次 AI 请求只发送第一段 WAV/MP3 语音。"
                } else {
                    val format = audioFormat(attachment)
                    if (format == null) {
                        notes += "语音《${attachment.name}》已附加；当前语音理解只直接发送 WAV/MP3，其它格式不会假装已识别。"
                    } else if (context == null) {
                        notes += "语音《${attachment.name}》已附加，但当前调用没有文件读取上下文。"
                    } else {
                        val bytes = readAttachmentBytes(context, attachment, MAX_AUDIO_BYTES)
                        if (bytes == null) {
                            notes += "语音《${attachment.name}》未送入语音模型：文件不可读取或超过 16 MB。"
                        } else {
                            audio = AiAudioPayload(Base64.encodeToString(bytes, Base64.NO_WRAP), format)
                        }
                    }
                }
            }

            attachment.kind == ChatAttachmentKind.VIDEO || lowerMime.startsWith("video/") -> {
                notes += "视频附件《${attachment.name}》已保存在会话中（${attachment.mimeType}，${formatAttachmentSize(attachment.size)}）；当前通用模型请求未直接读取视频内容，请不要假设已经看过视频。"
            }

            else -> {
                notes += "文件附件《${attachment.name}》已保存在会话中（${attachment.mimeType}，${formatAttachmentSize(attachment.size)}）；当前通用模型请求未直接读取文件内容，请不要假设已经读取。"
            }
        }
    }

    var prompt = parsed.text.trim()
    if (prompt.isBlank()) {
        prompt = when {
            audio != null -> "请理解我发送的语音并回复。"
            images.isNotEmpty() -> "请查看我发送的图片，并根据图片内容回复。"
            else -> "我发送了附件，请确认收到；对没有直接提供内容的附件，不要假设已经读取或看过。"
        }
    }
    if (notes.isNotEmpty()) {
        prompt += "\n\n附件处理说明：\n" + notes.joinToString("\n") { "- $it" }
    }
    return PreparedAiContent(prompt = prompt, images = images, audio = audio)
}


fun formatAttachmentSize(size: Long): String = when {
    size < 0L -> "大小未知"
    size < 1024L -> "$size B"
    size < 1024L * 1024L -> "%.1f KB".format(size / 1024.0)
    else -> "%.1f MB".format(size / (1024.0 * 1024.0))
}


private fun audioFormat(attachment: ChatAttachment): String? {
    val mime = attachment.mimeType.lowercase()
    val name = attachment.name.lowercase()
    return when {
        mime in setOf("audio/wav", "audio/x-wav", "audio/wave") || name.endsWith(".wav") -> "wav"
        mime == "audio/mpeg" || name.endsWith(".mp3") -> "mp3"
        else -> null
    }
}


private fun readAttachmentBytes(context: Context, attachment: ChatAttachment, limit: Int): ByteArray? {
    if (attachment.size > limit) return null
    val uri = runCatching { Uri.parse(attachment.uri) }.getOrNull() ?: return null
    return runCatching {
        context.contentResolver.openInputStream(uri)?.use { input ->
            val out = ByteArrayOutputStream()
            val buffer = ByteArray(64 * 1024)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                if (total > limit) return@use null
                out.write(buffer, 0, count)
            }
            out.toByteArray()
        }
    }.getOrNull()
}
