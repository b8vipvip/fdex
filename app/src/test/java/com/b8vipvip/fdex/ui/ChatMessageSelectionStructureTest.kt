package com.b8vipvip.fdex.ui

import java.nio.file.Files
import java.nio.file.Path
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatMessageSelectionStructureTest {
    @Test
    fun persistedMessageBodiesUseOneNativeSelectionBoundaryBeforeActions() {
        val source = mainSource("ChatMessageActions.kt")
        assertTrue(source.contains("androidx.compose.foundation.text.selection.SelectionContainer"))

        val employeeBlock = source.substringBetween(
            "internal fun ActionableEmployeeChatMessage(",
            "internal fun ActionableGroupChatMessage(",
        )
        assertTrue(employeeBlock.countOccurrences("SelectableMessageBody {") == 1)
        assertTrue(employeeBlock.indexOf("SelectableMessageBody {") < employeeBlock.indexOf("MessageActionBar("))

        val groupBlock = source.substringBetween(
            "internal fun ActionableGroupChatMessage(",
            "internal fun SelectableMessageBody(",
        )
        assertTrue(groupBlock.countOccurrences("SelectableMessageBody {") == 3)
        assertTrue(groupBlock.lastIndexOf("SelectableMessageBody {") < groupBlock.indexOf("MessageActionBar("))

        val wrapperBlock = source.substringBetween(
            "internal fun SelectableMessageBody(",
            "private fun MessageActionBar(",
        )
        assertTrue(wrapperBlock.contains("SelectionContainer(content = content)"))
        assertFalse(wrapperBlock.contains("MessageActionBar"))
    }

    @Test
    fun selectableUiConsumesParsedVisibleTextInsteadOfOpaqueMarkers() {
        val attachmentSource = mainSource("AttachmentChatComposer.kt")
        val userMessageBlock = attachmentSource.substringAfter("internal fun AttachmentUserMessage(content: String)")
        assertTrue(userMessageBlock.contains("parseChatContent(content)"))
        assertTrue(userMessageBlock.contains("parsed.text"))
        assertFalse(userMessageBlock.contains("Text(content)"))

        val assistantSource = mainSource("RichAssistantContent.kt")
        val assistantBlock = assistantSource.substringAfter("internal fun RichAssistantMessage(")
        assertTrue(assistantBlock.contains("parseRichAssistantContent(content)"))
        assertTrue(assistantBlock.contains("MarkdownText(parsed.markdown"))
        assertFalse(assistantBlock.contains("MarkdownText(content"))
    }

    private fun mainSource(fileName: String): String {
        val relative = Path.of("src", "main", "java", "com", "b8vipvip", "fdex", "ui", fileName)
        val candidates = listOf(relative, Path.of("app").resolve(relative))
        val path = candidates.firstOrNull { Files.exists(it) }
            ?: error("Unable to locate Android UI source $fileName from ${Path.of("").toAbsolutePath()}")
        return Files.readString(path)
    }

    private fun String.substringBetween(start: String, end: String): String {
        val startIndex = indexOf(start)
        val endIndex = indexOf(end, startIndex + start.length)
        require(startIndex >= 0 && endIndex > startIndex) { "Missing source markers: $start .. $end" }
        return substring(startIndex, endIndex)
    }

    private fun String.countOccurrences(needle: String): Int =
        windowed(needle.length, step = 1, partialWindows = false).count { it == needle }
}
