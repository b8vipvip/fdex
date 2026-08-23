package com.b8vipvip.fdex.ui

import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.GroupMessage
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationContextTest {
    @Test
    fun employeeContextKeepsRecentTurnsInOrderAndExcludesCurrentMessage() {
        val messages = listOf(
            ChatMessage(1, 7, "user", "第一问", "t1"),
            ChatMessage(2, 7, "employee", "第一答", "t2"),
            ChatMessage(3, 7, "user", "那第二个呢", "t3"),
        )
        val context = recentEmployeeConversationContext(messages, excludeMessageId = 3)
        assertTrue(context.contains("用户：第一问"))
        assertTrue(context.contains("员工：第一答"))
        assertFalse(context.contains("那第二个呢"))
        assertTrue(context.indexOf("第一问") < context.indexOf("第一答"))
    }

    @Test
    fun groupContextPreservesEmployeeNamesAndCurrentThreadMeaning() {
        val messages = listOf(
            GroupMessage(1, 9, "user", "我", "先分析需求", "t1"),
            GroupMessage(2, 9, "employee", "小策", "我建议先拆目标", "t2"),
            GroupMessage(3, 9, "user", "我", "继续", "t3"),
        )
        val context = recentGroupConversationContext(messages, excludeMessageId = 3)
        assertTrue(context.contains("用户：先分析需求"))
        assertTrue(context.contains("小策：我建议先拆目标"))
        assertFalse(context.contains("用户：继续"))
    }

    @Test
    fun quoteRemovesOpaqueAttachmentMarkerAndPreservesDraft() {
        val content = "请看这个\n\n[[FDEX_ATTACHMENTS_V1:YWJjZA==]]"
        val draft = quoteMessageIntoDraft("小策", content, "我的追问")
        assertTrue(draft.contains("> 引用 小策"))
        assertTrue(draft.contains("> 请看这个"))
        assertTrue(draft.contains("[包含附件]"))
        assertTrue(draft.endsWith("我的追问"))
        assertFalse(draft.contains("FDEX_ATTACHMENTS_V1"))
    }

    @Test
    fun contextUsesNewestMessagesWhenBudgetIsSmall() {
        val messages = (1L..8L).map {
            ChatMessage(it, 1, if (it % 2L == 0L) "employee" else "user", "消息$it-${"x".repeat(80)}", "t$it")
        }
        val context = recentEmployeeConversationContext(messages, maxMessages = 8, maxChars = 260)
        assertTrue(context.contains("消息8"))
        assertFalse(context.contains("消息1"))
    }
}
