package com.b8vipvip.fdex.diagnostics

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ClientRuntimeLogTest {
    @Test
    fun sanitizeTextRedactsCommonSecrets() {
        val safe = ClientRuntimeLog.sanitizeText(
            "authorization: Bearer abcdefghijklmnopqrstuvwxyz password=hunter2 api_key=sk-secret-value",
        )
        assertFalse(safe.contains("abcdefghijklmnopqrstuvwxyz"))
        assertFalse(safe.contains("hunter2"))
        assertFalse(safe.contains("sk-secret-value"))
        assertTrue(safe.contains("[REDACTED]"))
    }
}
