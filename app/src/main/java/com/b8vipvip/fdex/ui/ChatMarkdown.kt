package com.b8vipvip.fdex.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private enum class ChatMarkdownKind { BLANK, HEADING, QUOTE, BULLET, NUMBERED, CODE, TABLE, DIVIDER, TEXT }
private data class ChatMarkdownLine(
    val kind: ChatMarkdownKind,
    val text: String,
    val level: Int = 0,
    val language: String = "",
)

@Composable
internal fun ChatMarkdownText(
    markdown: String,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onSurface,
) {
    val lines = remember(markdown) { parseChatMarkdown(markdown) }
    val clipboard = LocalClipboardManager.current
    Column(modifier) {
        lines.forEach { line ->
            when (line.kind) {
                ChatMarkdownKind.BLANK -> Spacer(Modifier.height(9.dp))
                ChatMarkdownKind.HEADING -> Text(
                    text = chatInlineMarkdown(line.text),
                    fontWeight = FontWeight.Bold,
                    fontSize = when (line.level) {
                        1 -> 22.sp
                        2 -> 19.sp
                        3 -> 17.sp
                        else -> 16.sp
                    },
                    lineHeight = when (line.level) {
                        1 -> 29.sp
                        2 -> 26.sp
                        else -> 24.sp
                    },
                    color = color,
                    modifier = Modifier.padding(top = 8.dp, bottom = 4.dp),
                )
                ChatMarkdownKind.QUOTE -> Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFFF6F6F6), RoundedCornerShape(8.dp))
                        .padding(horizontal = 12.dp, vertical = 9.dp),
                ) {
                    Text(
                        text = chatInlineMarkdown(line.text),
                        color = color.copy(alpha = .82f),
                        fontSize = 16.sp,
                        lineHeight = 24.sp,
                    )
                }
                ChatMarkdownKind.BULLET -> Row(
                    modifier = Modifier.fillMaxWidth().padding(start = 4.dp, top = 2.dp, bottom = 2.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Text("•", fontSize = 16.sp, lineHeight = 24.sp, color = color)
                    Text(
                        text = chatInlineMarkdown(line.text),
                        fontSize = 16.sp,
                        lineHeight = 24.sp,
                        color = color,
                        modifier = Modifier.padding(start = 8.dp),
                    )
                }
                ChatMarkdownKind.NUMBERED -> Text(
                    text = chatInlineMarkdown(line.text),
                    fontSize = 16.sp,
                    lineHeight = 24.sp,
                    color = color,
                    modifier = Modifier.padding(start = 4.dp, top = 2.dp, bottom = 2.dp),
                )
                ChatMarkdownKind.CODE -> Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 5.dp)
                        .background(Color(0xFFF3F3F3), RoundedCornerShape(12.dp)),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(Color(0xFFE9E9E9), RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp))
                            .padding(horizontal = 12.dp, vertical = 7.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = line.language.ifBlank { "代码" },
                            color = color.copy(alpha = .68f),
                            style = MaterialTheme.typography.labelSmall,
                        )
                        Text(
                            text = "复制",
                            color = color.copy(alpha = .72f),
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier
                                .clickable { clipboard.setText(AnnotatedString(line.text)) }
                                .padding(horizontal = 5.dp, vertical = 3.dp),
                        )
                    }
                    Text(
                        text = line.text,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 13.sp,
                        lineHeight = 20.sp,
                        color = color,
                        modifier = Modifier.fillMaxWidth().padding(12.dp),
                    )
                }
                ChatMarkdownKind.TABLE -> Text(
                    text = line.text,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    lineHeight = 20.sp,
                    color = color,
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFFF7F7F7), RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 4.dp),
                )
                ChatMarkdownKind.DIVIDER -> HorizontalDivider(
                    modifier = Modifier.padding(vertical = 9.dp),
                    color = color.copy(alpha = .12f),
                )
                ChatMarkdownKind.TEXT -> Text(
                    text = chatInlineMarkdown(line.text),
                    fontSize = 16.sp,
                    lineHeight = 24.sp,
                    color = color,
                    modifier = Modifier.padding(vertical = 1.dp),
                )
            }
        }
    }
}

private fun parseChatMarkdown(markdown: String): List<ChatMarkdownLine> {
    val out = mutableListOf<ChatMarkdownLine>()
    var inCode = false
    var codeLanguage = ""
    val code = StringBuilder()

    markdown.replace("\r\n", "\n").replace('\r', '\n').split('\n').forEach { raw ->
        val trimmed = raw.trimEnd()
        val marker = trimmed.trimStart()
        if (marker.startsWith("```")) {
            if (inCode) {
                out += ChatMarkdownLine(
                    kind = ChatMarkdownKind.CODE,
                    text = code.toString().trimEnd(),
                    language = prettyChatLanguage(codeLanguage),
                )
                code.clear()
                codeLanguage = ""
                inCode = false
            } else {
                inCode = true
                val suffix = marker.removePrefix("```").trim()
                val language = suffix.takeWhile { !it.isWhitespace() && it != '#' }
                codeLanguage = language
                val remainder = suffix.removePrefix(language).trimStart()
                if (remainder.isNotBlank()) code.append(remainder).append('\n')
            }
            return@forEach
        }
        if (inCode) {
            code.append(trimmed).append('\n')
            return@forEach
        }
        if (trimmed.isBlank()) {
            out += ChatMarkdownLine(ChatMarkdownKind.BLANK, "")
            return@forEach
        }
        if (Regex("^\\s*([-*_])\\1\\1+\\s*$").matches(trimmed)) {
            out += ChatMarkdownLine(ChatMarkdownKind.DIVIDER, "")
            return@forEach
        }
        val heading = Regex("^(#{1,6})\\s+(.+)$").find(trimmed)
        if (heading != null) {
            out += ChatMarkdownLine(ChatMarkdownKind.HEADING, heading.groupValues[2], heading.groupValues[1].length)
            return@forEach
        }
        val quote = Regex("^\\s*>\\s?(.+)$").find(trimmed)
        if (quote != null) {
            out += ChatMarkdownLine(ChatMarkdownKind.QUOTE, quote.groupValues[1])
            return@forEach
        }
        val bullet = Regex("^\\s*[-*+]\\s+(.+)$").find(trimmed)
        if (bullet != null) {
            out += ChatMarkdownLine(ChatMarkdownKind.BULLET, bullet.groupValues[1])
            return@forEach
        }
        val numbered = Regex("^\\s*(\\d+\\.)\\s+(.+)$").find(trimmed)
        if (numbered != null) {
            out += ChatMarkdownLine(ChatMarkdownKind.NUMBERED, "${numbered.groupValues[1]}  ${numbered.groupValues[2]}")
            return@forEach
        }
        if (trimmed.count { it == '|' } >= 2) {
            out += ChatMarkdownLine(ChatMarkdownKind.TABLE, trimmed)
            return@forEach
        }
        out += ChatMarkdownLine(ChatMarkdownKind.TEXT, trimmed)
    }

    if (inCode && code.isNotEmpty()) {
        out += ChatMarkdownLine(
            kind = ChatMarkdownKind.CODE,
            text = code.toString().trimEnd(),
            language = prettyChatLanguage(codeLanguage),
        )
    }
    return out
}

private fun prettyChatLanguage(value: String): String = when (value.lowercase()) {
    "md", "markdown" -> "Markdown"
    "kt", "kotlin" -> "Kotlin"
    "js", "javascript" -> "JavaScript"
    "ts", "typescript" -> "TypeScript"
    "py", "python" -> "Python"
    "json" -> "JSON"
    "sh", "bash", "shell" -> "Shell"
    "html" -> "HTML"
    "css" -> "CSS"
    "sql" -> "SQL"
    "" -> "代码"
    else -> value
}

private val chatBoldPattern = Regex("\\*\\*(.+?)\\*\\*")
private val chatCodePattern = Regex("`([^`]+)`")
private val chatLinkPattern = Regex("\\[([^\\]]+)]\\(([^)]+)\\)")
private val chatItalicPattern = Regex("(?<!\\*)\\*([^*]+)\\*(?!\\*)")

private fun chatInlineMarkdown(source: String): AnnotatedString = buildAnnotatedString {
    var cursor = 0
    while (cursor < source.length) {
        val candidates = listOfNotNull(
            chatBoldPattern.find(source, cursor)?.let { "bold" to it },
            chatCodePattern.find(source, cursor)?.let { "code" to it },
            chatLinkPattern.find(source, cursor)?.let { "link" to it },
            chatItalicPattern.find(source, cursor)?.let { "italic" to it },
        )
        val candidate = candidates.minByOrNull { it.second.range.first }
        if (candidate == null) {
            append(source.substring(cursor))
            break
        }
        val (kind, match) = candidate
        if (match.range.first > cursor) append(source.substring(cursor, match.range.first))
        when (kind) {
            "bold" -> pushStyle(SpanStyle(fontWeight = FontWeight.Bold))
            "code" -> pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = Color(0xFFE8E8E8)))
            "link" -> pushStyle(SpanStyle(color = Color(0xFF2563EB), textDecoration = TextDecoration.Underline))
            "italic" -> pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
        }
        append(match.groupValues[1])
        pop()
        cursor = match.range.last + 1
    }
}
