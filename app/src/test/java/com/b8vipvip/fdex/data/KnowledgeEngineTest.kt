package com.b8vipvip.fdex.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KnowledgeEngineTest {
    @Test
    fun `casual greeting is separated from business knowledge`() {
        val casual = KnowledgeEngine.fallbackDraft("用户：你好呀\n小知：你好，有什么可以帮你？", "小知")
        assertEquals(KnowledgeRooms.CASUAL, casual.room)
        assertTrue(casual.title.contains("日常闲聊"))
    }

    @Test
    fun `short technical question is not misclassified as casual`() {
        val draft = KnowledgeEngine.fallbackDraft("用户：你好，API 为什么报错？\n小程：我先检查日志。", "小程")
        assertEquals(KnowledgeRooms.TECHNICAL, draft.room)
    }

    @Test
    fun `knowledge search uses summary and generated keywords`() {
        val android = sampleEntry(
            id = 1,
            room = KnowledgeRooms.TECHNICAL,
            title = "Android 图片流式收尾",
            summary = "修复 SSE 已生成正文但客户端仍显示正在分析的问题",
            keywords = listOf("Android", "SSE", "图片识别"),
        )
        val finance = sampleEntry(
            id = 2,
            room = KnowledgeRooms.FINANCE,
            title = "季度预算",
            summary = "整理现金流和广告成本",
            keywords = listOf("预算", "成本"),
        )
        val hits = KnowledgeEngine.search(listOf(finance, android), "图片 SSE 卡在正在分析")
        assertFalse(hits.isEmpty())
        assertEquals(android.id, hits.first().entry.id)
    }

    @Test
    fun `permissions default to own chat only and no shared knowledge access`() {
        val permissions = EmployeePermissions()
        assertFalse(permissions.knowledgeRead)
        assertFalse(permissions.knowledgeWrite)
        assertEquals(EmployeeChatAccess.SELF, permissions.chatAccessMode)
        assertTrue(permissions.readableEmployeeIds.isEmpty())
    }

    private fun sampleEntry(
        id: Long,
        room: String,
        title: String,
        summary: String,
        keywords: List<String>,
    ) = KnowledgeEntry(
        id = id,
        scopeKey = "fdex-account:test",
        wing = "company",
        room = room,
        title = title,
        summary = summary,
        keywords = keywords,
        rawText = summary,
        conversationId = "test:$id",
        source = "manual",
        sourceEmployeeId = null,
        sourceEmployeeName = "",
        sourceMessageIds = emptyList(),
        contentHash = id.toString(),
        sharedForAgents = true,
        needsEnrichment = false,
        createdAt = "2026-08-18T00:00:00Z",
        updatedAt = "2026-08-18T00:00:00Z",
    )
}
