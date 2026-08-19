package com.b8vipvip.fdex.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatRequestPolicyTest {
    @Test
    fun `plain text never claims an attachment is being prepared`() {
        assertEquals("正在连接 AI…", initialAiStatus("帮我统计之前的项目记录"))
        assertEquals(
            "FDEX 服务端已接收文本请求，正在选择模型…",
            normalizeAiStatus("FDEX 服务端已接收请求，正在解析附件与选择模型…", hasAttachments = false),
        )
        assertEquals(
            "已选择 text 路由，正在连接 AI…",
            normalizeAiStatus("附件解析完成，已选择 text 路由，正在连接 AI…", hasAttachments = false),
        )
    }

    @Test
    fun `attachment status is preserved when attachments really exist`() {
        val original = "FDEX 服务端已接收请求，正在解析附件与选择模型…"
        assertEquals(original, normalizeAiStatus(original, hasAttachments = true))
    }

    @Test
    fun `connection abort does not trigger a second long request`() {
        assertFalse(shouldRetryNonStreamAfterStreamFailure("Software caused connection abort（请求ID：000f36ef）"))
        assertFalse(shouldRetryNonStreamAfterStreamFailure("所有 AI 供应商均调用失败：HTTP 503"))
    }

    @Test
    fun `missing sse tail may use one compatibility fallback`() {
        assertTrue(shouldRetryNonStreamAfterStreamFailure("流式连接提前结束，未收到正文或媒体结果"))
        assertTrue(shouldRetryNonStreamAfterStreamFailure("服务端没有返回流式响应正文"))
    }

    @Test
    fun `project history questions request local project records`() {
        assertTrue(shouldIncludeProjectRecords("帮我统计我之前的项目记录"))
        assertTrue(shouldIncludeProjectRecords("汇总历史工作记录"))
        assertFalse(shouldIncludeProjectRecords("今天天气怎么样"))
    }
}
