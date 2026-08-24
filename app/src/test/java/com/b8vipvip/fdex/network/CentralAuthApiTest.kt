package com.b8vipvip.fdex.network

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class CentralAuthApiTest {
    @Test
    fun parsesCentralSession() {
        val json = JSONObject(
            """
            {
              "user":{"id":"usr_abc","email":"user@example.com","name":"User","company_name":"FDEX"},
              "access_token":"access-token-value",
              "refresh_token":"refresh-token-value",
              "access_expires_at":"2026-08-24T12:00:00+00:00",
              "refresh_expires_at":"2026-09-24T12:00:00+00:00"
            }
            """.trimIndent(),
        )
        val session = CentralAuthApi.parseSession(json)
        assertEquals("usr_abc", session.user.id)
        assertEquals("user@example.com", session.user.email)
        assertEquals("FDEX", session.user.companyName)
        assertEquals("access-token-value", session.accessToken)
        assertEquals("refresh-token-value", session.refreshToken)
    }
}
