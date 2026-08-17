package com.b8vipvip.fdex.network

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.math.roundToInt

private const val ATTACHMENT_MARKER = "FDEX_ATTACHMENTS_V1"
private const val MAX_IMAGE_BYTES = 8 * 1024 * 1024
private const val IMAGE_COMPRESS_THRESHOLD_BYTES = 1024 * 1024
private const val MAX_IMAGE_EDGE = 1600
private const val MAX_AUDIO_BYTES = 16 * 1024 * 1024
private const val MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
private const val MAX_DOCUMENT_TOTAL_BYTES = 12 * 1024 * 1024
private const val MAX_DOCUMENTS = 3
private const val MAX_IMAGES = 4
private const val MAX_VIDEO_FRAMES = 4
private const val MAX_VIDEO_FRAME_EDGE = 1280


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


data class AiDocumentPayload(
    val name: String,
    val mimeType: String,
    val data: String,
)


data class PreparedAiContent(
    val prompt: String,
    val images: List<AiImagePayload> = emptyList(),
    val audio: AiAudioPayload? = null,
    val documents: List<AiDocumentPayload> = emptyList(),
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


fun chatAttachmentKindFor(name: String, mimeType: String): ChatAttachmentKind {
    val mime = mimeType.lowercase()
    val lowerName = name.lowercase()
    return when {
        mime.startsWith("image/") -> ChatAttachmentKind.IMAGE
        mime.startsWith("video/") -> ChatAttachmentKind.VIDEO
        mime.startsWith("audio/") -> ChatAttachmentKind.AUDIO
        lowerName.endsWith(".png") || lowerName.endsWith(".jpg") || lowerName.endsWith(".jpeg") || lowerName.endsWith(".webp") -> ChatAttachmentKind.IMAGE
        lowerName.endsWith(".mp4") || lowerName.endsWith(".webm") || lowerName.endsWith(".mov") || lowerName.endsWith(".mkv") -> ChatAttachmentKind.VIDEO
        lowerName.endsWith(".wav") || lowerName.endsWith(".mp3") || lowerName.endsWith(".m4a") || lowerName.endsWith(".aac") -> ChatAttachmentKind.AUDIO
        else -> ChatAttachmentKind.FILE
    }
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
                    .put("kind", attachment.kind.wireName),
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
                    ),
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
    val documents = mutableListOf<AiDocumentPayload>()
    var documentBytes = 0
    var audio: AiAudioPayload? = null
    val notes = mutableListOf<String>()

    parsed.attachments.forEach { attachment ->
        val lowerMime = attachment.mimeType.lowercase()
        when {
            attachment.kind == ChatAttachmentKind.IMAGE || lowerMime.startsWith("image/") -> {
                if (images.size >= MAX_IMAGES) {
                    notes += "图片《${attachment.name}》未送入视觉模型：一次最多处理 $MAX_IMAGES 张图片/视频帧。"
                } else if (context == null) {
                    notes += "图片《${attachment.name}》已附加，但当前调用没有文件读取上下文。"
                } else {
                    val bytes = readAttachmentBytes(context, attachment, MAX_IMAGE_BYTES)
                    if (bytes == null) {
                        notes += "图片《${attachment.name}》未送入视觉模型：文件不可读取或超过 8 MB。"
                    } else {
                        val mime = lowerMime.takeIf { it.startsWith("image/") } ?: "image/jpeg"
                        images += imageBytesToPayload(bytes, mime)
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
                if (context == null) {
                    notes += "视频《${attachment.name}》已附加，但当前调用没有文件读取上下文。"
                } else {
                    val remaining = (MAX_IMAGES - images.size).coerceAtLeast(0)
                    if (remaining == 0) {
                        notes += "视频《${attachment.name}》未抽帧：图片/视频帧总数已达到 $MAX_IMAGES。"
                    } else {
                        val frames = extractVideoFrames(context, attachment, minOf(MAX_VIDEO_FRAMES, remaining))
                        if (frames.isEmpty()) {
                            notes += "视频《${attachment.name}》无法读取有效画面，未假装已经分析。"
                        } else {
                            images += frames
                            notes += "视频《${attachment.name}》已在手机端抽取 ${frames.size} 个代表画面送入视觉模型；这是关键帧分析，不代表逐帧读取，也不包含视频音轨。"
                        }
                    }
                }
            }

            else -> {
                if (context == null) {
                    notes += "文件《${attachment.name}》已附加，但当前调用没有文件读取上下文。"
                } else if (documents.size >= MAX_DOCUMENTS) {
                    notes += "文件《${attachment.name}》未发送：一次最多解析 $MAX_DOCUMENTS 份文档。"
                } else {
                    val remaining = MAX_DOCUMENT_TOTAL_BYTES - documentBytes
                    val limit = minOf(MAX_DOCUMENT_BYTES, remaining)
                    if (limit <= 0) {
                        notes += "文件《${attachment.name}》未发送：本次文档总大小已达到 12 MB。"
                    } else {
                        val bytes = readAttachmentBytes(context, attachment, limit)
                        if (bytes == null) {
                            notes += "文件《${attachment.name}》未发送：文件不可读取、单文件超过 8 MB，或本次文档总量超过 12 MB。"
                        } else {
                            documentBytes += bytes.size
                            documents += AiDocumentPayload(
                                name = attachment.name.take(240),
                                mimeType = attachment.mimeType.take(120),
                                data = Base64.encodeToString(bytes, Base64.NO_WRAP),
                            )
                            notes += "文件《${attachment.name}》已交给 FDEX 服务端做内存正文提取；不支持或提取失败的格式会明确标注，不会假装已经读取。"
                        }
                    }
                }
            }
        }
    }

    var prompt = parsed.text.trim()
    if (prompt.isBlank()) {
        prompt = when {
            audio != null -> "请理解我发送的语音并回复。"
            documents.isNotEmpty() && images.isNotEmpty() -> "请阅读我发送的文件正文，并结合图片或视频抽帧画面一起分析后回复。"
            documents.isNotEmpty() -> "请阅读我发送的文件正文，并根据文件实际内容回复。"
            images.isNotEmpty() -> "请查看我发送的图片或视频抽帧画面，并根据实际画面内容回复。"
            else -> "我发送了附件；只根据实际成功读取到的内容回答，不要假设未读取的附件内容。"
        }
    }
    if (notes.isNotEmpty()) {
        prompt += "\n\n附件处理说明：\n" + notes.joinToString("\n") { "- $it" }
    }
    return PreparedAiContent(prompt = prompt, images = images, audio = audio, documents = documents)
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


private fun imageBytesToPayload(bytes: ByteArray, mimeType: String): AiImagePayload {
    if (bytes.size <= IMAGE_COMPRESS_THRESHOLD_BYTES) {
        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
        return AiImagePayload("data:$mimeType;base64,$encoded")
    }

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
        return AiImagePayload("data:$mimeType;base64,$encoded")
    }

    var sampleSize = 1
    while (maxOf(bounds.outWidth / sampleSize, bounds.outHeight / sampleSize) > MAX_IMAGE_EDGE * 2) {
        sampleSize *= 2
    }
    val decoded = BitmapFactory.decodeByteArray(
        bytes,
        0,
        bytes.size,
        BitmapFactory.Options().apply { inSampleSize = sampleSize },
    ) ?: run {
        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
        return AiImagePayload("data:$mimeType;base64,$encoded")
    }

    val longest = maxOf(decoded.width, decoded.height)
    val scaled = if (longest > MAX_IMAGE_EDGE) {
        val ratio = MAX_IMAGE_EDGE.toFloat() / longest.toFloat()
        Bitmap.createScaledBitmap(
            decoded,
            (decoded.width * ratio).roundToInt().coerceAtLeast(1),
            (decoded.height * ratio).roundToInt().coerceAtLeast(1),
            true,
        )
    } else {
        decoded
    }

    return try {
        val out = ByteArrayOutputStream()
        if (!scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)) {
            val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
            AiImagePayload("data:$mimeType;base64,$encoded")
        } else {
            val encoded = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
            AiImagePayload("data:image/jpeg;base64,$encoded")
        }
    } finally {
        if (scaled !== decoded) scaled.recycle()
        decoded.recycle()
    }
}


private fun extractVideoFrames(context: Context, attachment: ChatAttachment, maxFrames: Int): List<AiImagePayload> {
    if (maxFrames <= 0) return emptyList()
    val uri = runCatching { Uri.parse(attachment.uri) }.getOrNull() ?: return emptyList()
    val retriever = MediaMetadataRetriever()
    return try {
        retriever.setDataSource(context, uri)
        val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
            ?.toLongOrNull()
            ?.coerceAtLeast(0L)
            ?: 0L
        val fractions = when (maxFrames) {
            1 -> listOf(0.5)
            2 -> listOf(0.2, 0.8)
            3 -> listOf(0.15, 0.5, 0.85)
            else -> listOf(0.08, 0.35, 0.65, 0.92)
        }
        fractions.take(maxFrames).mapNotNull { fraction ->
            val timeUs = if (durationMs > 0) (durationMs * fraction * 1000.0).toLong() else 0L
            val frame = runCatching {
                retriever.getFrameAtTime(timeUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
            }.getOrNull() ?: return@mapNotNull null
            bitmapToImagePayload(frame)
        }
    } catch (_: Exception) {
        emptyList()
    } finally {
        runCatching { retriever.release() }
    }
}


private fun bitmapToImagePayload(bitmap: Bitmap): AiImagePayload? {
    val longest = maxOf(bitmap.width, bitmap.height)
    val scaled = if (longest > MAX_VIDEO_FRAME_EDGE) {
        val ratio = MAX_VIDEO_FRAME_EDGE.toFloat() / longest.toFloat()
        Bitmap.createScaledBitmap(
            bitmap,
            (bitmap.width * ratio).roundToInt().coerceAtLeast(1),
            (bitmap.height * ratio).roundToInt().coerceAtLeast(1),
            true,
        )
    } else {
        bitmap
    }
    return try {
        val out = ByteArrayOutputStream()
        if (!scaled.compress(Bitmap.CompressFormat.JPEG, 82, out)) return null
        val encoded = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        AiImagePayload("data:image/jpeg;base64,$encoded", detail = "low")
    } finally {
        if (scaled !== bitmap) scaled.recycle()
        bitmap.recycle()
    }
}


private fun readAttachmentBytes(context: Context, attachment: ChatAttachment, limit: Int): ByteArray? {
    if (limit <= 0 || attachment.size > limit) return null
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
