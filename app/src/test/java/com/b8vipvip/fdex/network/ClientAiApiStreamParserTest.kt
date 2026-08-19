package com.b8vipvip.fdex.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientAiApiStreamParserTest {
    @Test
    fun contentJsonDataIsExtractedWithoutAndroidJsonRuntime() {
        val raw = ClientAiApi.extractSseData(
            "data: {\"type\":\"content\",\"delta\":\"图片识别完成\"}",
        )
        assertEquals("{\"type\":\"content\",\"delta\":\"图片识别完成\"}", raw)
    }

    @Test
    fun explicitDoneJsonDataIsExtracted() {
        val raw = ClientAiApi.extractSseData(
            "data: {\"type\":\"done\",\"model\":\"gpt-5.5-mini\",\"latency_ms\":19455}",
        )
        assertEquals(
            "{\"type\":\"done\",\"model\":\"gpt-5.5-mini\",\"latency_ms\":19455}",
            raw,
        )
    }

    @Test
    fun trailingDoneMarkerIsExtracted() {
        assertEquals("[DONE]", ClientAiApi.extractSseData("data: [DONE]"))
    }

    @Test
    fun leadingWhitespaceDoesNotBreakSseExtraction() {
        val raw = ClientAiApi.extractSseData(
            "   data: {\"type\":\"status\",\"status\":\"正在分析 1 幅图片\"}",
        )
        assertEquals("{\"type\":\"status\",\"status\":\"正在分析 1 幅图片\"}", raw)
    }

    @Test
    fun nonDataSseLineIsIgnored() {
        assertNull(ClientAiApi.extractSseData(": heartbeat"))
        assertNull(ClientAiApi.extractSseData("event: message"))
        assertNull(ClientAiApi.extractSseData(""))
    }

    @Test
    fun attachmentIdleBudgetOutlivesBrowserUploadPreparation() {
        assertTrue(ClientAiApi.ATTACHMENT_STREAM_READ_TIMEOUT_SECONDS >= 120L)
    }
}
