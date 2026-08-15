package com.b8vipvip.fdex.network

import android.util.Base64
import org.json.JSONObject

private const val MEDIA_PREFIX = "[[FDEX_MEDIA_V1:"
private const val MEDIA_SUFFIX = "]]"
private val mediaMarkerRegex = Regex("\\[\\[FDEX_MEDIA_V1:([A-Za-z0-9_\\-=]+)]]")

data class AiMediaResult(
    val kind: String,
    val url: String,
    val mimeType: String = "",
    val transcript: String = "",
    val revisedPrompt: String = "",
)

data class RichAssistantContent(
    val markdown: String,
    val media: List<AiMediaResult>,
)

fun encodeAiMediaMarker(media: AiMediaResult): String {
    val raw = JSONObject()
        .put("kind", media.kind)
        .put("url", media.url)
        .put("mime", media.mimeType)
        .put("transcript", media.transcript)
        .put("revised_prompt", media.revisedPrompt)
        .toString()
    val encoded = Base64.encodeToString(
        raw.toByteArray(Charsets.UTF_8),
        Base64.URL_SAFE or Base64.NO_WRAP,
    )
    return "$MEDIA_PREFIX$encoded$MEDIA_SUFFIX"
}

fun parseRichAssistantContent(content: String): RichAssistantContent {
    val media = mutableListOf<AiMediaResult>()
    mediaMarkerRegex.findAll(content).forEach { match ->
        val raw = runCatching {
            String(
                Base64.decode(match.groupValues[1], Base64.URL_SAFE or Base64.NO_WRAP),
                Charsets.UTF_8,
            )
        }.getOrNull() ?: return@forEach
        val json = runCatching { JSONObject(raw) }.getOrNull() ?: return@forEach
        val url = json.optString("url")
        if (url.isBlank()) return@forEach
        media += AiMediaResult(
            kind = json.optString("kind"),
            url = url,
            mimeType = json.optString("mime"),
            transcript = json.optString("transcript"),
            revisedPrompt = json.optString("revised_prompt"),
        )
    }

    var markdown = mediaMarkerRegex.replace(content, "").trim()
    media.forEach { item ->
        val escaped = Regex.escape(item.url)
        markdown = markdown
            .replace(Regex("(?m)^\\s*\\[[^]]*]\\($escaped\\)\\s*$"), "")
            .replace(Regex("(?m)^\\s*!\\[[^]]*]\\($escaped\\)\\s*$"), "")
    }
    markdown = markdown.replace(Regex("\\n{3,}"), "\n\n").trim()
    return RichAssistantContent(markdown, media.distinctBy { it.kind to it.url })
}
