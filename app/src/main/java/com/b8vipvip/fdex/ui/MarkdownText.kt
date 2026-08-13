package com.b8vipvip.fdex.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private enum class MarkdownKind { BLANK, HEADING, QUOTE, BULLET, NUMBERED, CODE, TABLE, TEXT }
private data class MarkdownLine(val kind: MarkdownKind, val text: String, val level: Int = 0)

@Composable
internal fun MarkdownText(
    markdown: String,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onSurface,
) {
    val lines = remember(markdown) { parseMarkdown(markdown) }
    Column(modifier) {
        lines.forEach { line ->
            when (line.kind) {
                MarkdownKind.BLANK -> Spacer(Modifier.height(8.dp))
                MarkdownKind.HEADING -> Text(
                    inlineMarkdown(line.text),
                    fontWeight = FontWeight.Bold,
                    fontSize = when (line.level) {
                        1 -> 23.sp
                        2 -> 20.sp
                        3 -> 18.sp
                        else -> 16.sp
                    },
                    color = color,
                    modifier = Modifier.padding(top = 6.dp, bottom = 3.dp),
                )
                MarkdownKind.QUOTE -> Text(
                    inlineMarkdown(line.text),
                    color = color.copy(alpha = .82f),
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFFF1F5F9), RoundedCornerShape(8.dp))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                )
                MarkdownKind.BULLET -> Text(
                    buildAnnotatedString {
                        append("•  ")
                        append(inlineMarkdown(line.text))
                    },
                    color = color,
                    modifier = Modifier.padding(start = 4.dp, top = 2.dp, bottom = 2.dp),
                )
                MarkdownKind.NUMBERED -> Text(
                    inlineMarkdown(line.text),
                    color = color,
                    modifier = Modifier.padding(start = 4.dp, top = 2.dp, bottom = 2.dp),
                )
                MarkdownKind.CODE -> Text(
                    line.text,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 13.sp,
                    color = color,
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color(0xFFF1F5F9), RoundedCornerShape(8.dp))
                        .padding(10.dp),
                )
                MarkdownKind.TABLE -> Text(
                    line.text,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 12.sp,
                    color = color,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
                MarkdownKind.TEXT -> Text(
                    inlineMarkdown(line.text),
                    color = color,
                    modifier = Modifier.padding(vertical = 2.dp),
                )
            }
        }
    }
}

private fun parseMarkdown(markdown: String): List<MarkdownLine> {
    val out = mutableListOf<MarkdownLine>()
    var inCode = false
    val code = StringBuilder()
    markdown.replace("\r\n", "\n").replace('\r', '\n').split('\n').forEach { raw ->
        val trimmed = raw.trimEnd()
        if (trimmed.trimStart().startsWith("```")) {
            if (inCode) {
                out += MarkdownLine(MarkdownKind.CODE, code.toString().trimEnd())
                code.clear()
                inCode = false
            } else {
                inCode = true
            }
            return@forEach
        }
        if (inCode) {
            code.append(trimmed).append('\n')
            return@forEach
        }
        if (trimmed.isBlank()) {
            out += MarkdownLine(MarkdownKind.BLANK, "")
            return@forEach
        }
        val heading = Regex("^(#{1,6})\\s+(.+)$").find(trimmed)
        if (heading != null) {
            out += MarkdownLine(MarkdownKind.HEADING, heading.groupValues[2], heading.groupValues[1].length)
            return@forEach
        }
        if (trimmed.startsWith("> ")) {
            out += MarkdownLine(MarkdownKind.QUOTE, trimmed.removePrefix("> "))
            return@forEach
        }
        val bullet = Regex("^\\s*[-*+]\\s+(.+)$").find(trimmed)
        if (bullet != null) {
            out += MarkdownLine(MarkdownKind.BULLET, bullet.groupValues[1])
            return@forEach
        }
        val numbered = Regex("^\\s*(\\d+\\.)\\s+(.+)$").find(trimmed)
        if (numbered != null) {
            out += MarkdownLine(MarkdownKind.NUMBERED, "${numbered.groupValues[1]}  ${numbered.groupValues[2]}")
            return@forEach
        }
        if (trimmed.count { it == '|' } >= 2) {
            out += MarkdownLine(MarkdownKind.TABLE, trimmed)
            return@forEach
        }
        out += MarkdownLine(MarkdownKind.TEXT, trimmed)
    }
    if (inCode && code.isNotEmpty()) out += MarkdownLine(MarkdownKind.CODE, code.toString().trimEnd())
    return out
}

private val boldPattern = Regex("\\*\\*(.+?)\\*\\*")
private val codePattern = Regex("`([^`]+)`")
private val linkPattern = Regex("\\[([^\\]]+)]\\(([^)]+)\\)")
private val italicPattern = Regex("(?<!\\*)\\*([^*]+)\\*(?!\\*)")

private fun inlineMarkdown(source: String): AnnotatedString = buildAnnotatedString {
    var cursor = 0
    while (cursor < source.length) {
        val candidates = listOfNotNull(
            boldPattern.find(source, cursor)?.let { "bold" to it },
            codePattern.find(source, cursor)?.let { "code" to it },
            linkPattern.find(source, cursor)?.let { "link" to it },
            italicPattern.find(source, cursor)?.let { "italic" to it },
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
            "code" -> pushStyle(SpanStyle(fontFamily = FontFamily.Monospace, background = Color(0xFFE2E8F0)))
            "link" -> pushStyle(SpanStyle(color = Color(0xFF2563EB), textDecoration = TextDecoration.Underline))
            "italic" -> pushStyle(SpanStyle(fontStyle = FontStyle.Italic))
        }
        append(match.groupValues[1])
        pop()
        cursor = match.range.last + 1
    }
}
