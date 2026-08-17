package com.b8vipvip.fdex.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientAiApiStreamParserTest {
    @Test
    fun contentEventIsParsedFromSseDataLine() {
        val parsed = ClientAiApi.parseSseLine(
            "data: {\"type\":\"content\",\"delta\":\"图片识别完成\"}",
        )
        assertTrue(parsed is SseLineResult.Event)
        val event = (parsed as SseLineResult.Event).event
        assertTrue(event is AiStreamEvent.Content)
        assertEquals("图片识别完成", (event as AiStreamEvent.Content).delta)
    }

    @Test
    fun explicitDoneEventIsParsedWithoutWaitingForDoneMarker() {
        val parsed = ClientAiApi.parseSseLine(
            "data: {\"type\":\"done\",\"model\":\"gpt-5.5-mini\",\"latency_ms\":19455}",
        )
        assertTrue(parsed is SseLineResult.Event)
        val event = (parsed as SseLineResult.Event).event
        assertTrue(event is AiStreamEvent.Done)
        assertEquals("gpt-5.5-mini", (event as AiStreamEvent.Done).model)
        assertEquals(19455, event.latencyMs)
    }

    @Test
    fun trailingDoneMarkerIsRecognized() {
        assertTrue(ClientAiApi.parseSseLine("data: [DONE]") === SseLineResult.DoneMarker)
    }

    @Test
    fun leadingWhitespaceDoesNotBreakSseParsing() {
        val parsed = ClientAiApi.parseSseLine(
            "   data: {\"type\":\"status\",\"status\":\"正在分析 1 幅图片\"}",
        )
        assertTrue(parsed is SseLineResult.Event)
        val event = (parsed as SseLineResult.Event).event
        assertTrue(event is AiStreamEvent.Status)
        assertEquals("正在分析 1 幅图片", (event as AiStreamEvent.Status).status)
    }
}
