package com.b8vipvip.fdex.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class CentralSessionExpiryTest {
    private val now = Instant.parse("2026-08-24T12:00:00Z")

    @Test
    fun refreshWindowStartsFiveMinutesBeforeExpiry() {
        assertFalse(sessionExpiresWithin("2026-08-24T12:10:01Z", 300, now))
        assertTrue(sessionExpiresWithin("2026-08-24T12:05:00Z", 300, now))
        assertTrue(sessionExpiresWithin("2026-08-24T12:05:00+00:00", 300, now))
    }

    @Test
    fun expiredAndMalformedValuesAreHandledSafely() {
        assertTrue(sessionExpiresWithin("2026-08-24T11:59:59Z", 0, now))
        assertFalse(sessionExpiresWithin("", 300, now))
        assertFalse(sessionExpiresWithin("not-an-instant", 300, now))
    }
}
