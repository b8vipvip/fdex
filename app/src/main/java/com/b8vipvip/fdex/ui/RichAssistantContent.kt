package com.b8vipvip.fdex.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.b8vipvip.fdex.network.AiMediaResult
import com.b8vipvip.fdex.network.parseRichAssistantContent

@Composable
internal fun RichAssistantMessage(
    content: String,
    modifier: Modifier = Modifier,
) {
    val parsed = remember(content) { parseRichAssistantContent(content) }
    Column(modifier) {
        if (parsed.markdown.isNotBlank()) {
            MarkdownText(parsed.markdown, modifier = Modifier.fillMaxWidth())
        }
        parsed.media.forEach { media ->
            when (media.kind.lowercase()) {
                "image" -> GeneratedImageCard(media)
                "audio" -> GeneratedAudioCard(media)
                else -> GeneratedMediaLink(media)
            }
        }
    }
}

@Composable
private fun GeneratedImageCard(media: AiMediaResult) {
    val uriHandler = LocalUriHandler.current
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 10.dp),
        shape = RoundedCornerShape(14.dp),
        tonalElevation = 1.dp,
    ) {
        Column {
            AsyncImage(
                model = media.url,
                contentDescription = "AI 生成图片",
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 160.dp, max = 480.dp)
                    .clip(RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp))
                    .clickable { runCatching { uriHandler.openUri(media.url) } },
                contentScale = ContentScale.Fit,
            )
            Text(
                text = media.revisedPrompt.ifBlank { "查看原图" },
                color = MaterialTheme.colorScheme.primary,
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { runCatching { uriHandler.openUri(media.url) } }
                    .padding(horizontal = 12.dp, vertical = 9.dp),
            )
        }
    }
}

@Composable
private fun GeneratedAudioCard(media: AiMediaResult) {
    val uriHandler = LocalUriHandler.current
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 10.dp)
            .clickable { runCatching { uriHandler.openUri(media.url) } },
        shape = RoundedCornerShape(14.dp),
        tonalElevation = 1.dp,
    ) {
        Column(Modifier.padding(12.dp)) {
            Icon(Icons.Default.PlayArrow, contentDescription = null)
            Text("播放语音", style = MaterialTheme.typography.titleSmall)
            if (media.transcript.isNotBlank()) {
                Text(
                    media.transcript,
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 5.dp),
                )
            }
        }
    }
}

@Composable
private fun GeneratedMediaLink(media: AiMediaResult) {
    val uriHandler = LocalUriHandler.current
    Text(
        "打开媒体结果",
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier
            .padding(top = 8.dp)
            .clickable { runCatching { uriHandler.openUri(media.url) } },
    )
}
