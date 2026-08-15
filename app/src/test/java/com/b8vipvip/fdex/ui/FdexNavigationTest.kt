package com.b8vipvip.fdex.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FdexNavigationTest {
    @Test
    fun `messages root lets Android exit app`() {
        assertFalse(
            shouldHandleSystemBack(
                route = Route.Messages,
                hasHistory = false,
                overlayOpen = false,
            )
        )
    }

    @Test
    fun `secondary page consumes system back even without history`() {
        assertTrue(
            shouldHandleSystemBack(
                route = Route.EmployeeChat(7L),
                hasHistory = false,
                overlayOpen = false,
            )
        )
        assertEquals(Route.Messages, fallbackBackTarget(Route.EmployeeChat(7L)))
    }

    @Test
    fun `top level tabs return to messages before app exits`() {
        listOf(Route.Work, Route.Discover, Route.Me).forEach { route ->
            assertTrue(shouldHandleSystemBack(route, hasHistory = false, overlayOpen = false))
            assertEquals(Route.Messages, fallbackBackTarget(route))
        }
    }

    @Test
    fun `register returns to login`() {
        assertTrue(shouldHandleSystemBack(Route.Register, hasHistory = false, overlayOpen = false))
        assertEquals(Route.Login, fallbackBackTarget(Route.Register))
    }

    @Test
    fun `existing history always consumes back outside login`() {
        assertTrue(shouldHandleSystemBack(Route.Messages, hasHistory = true, overlayOpen = false))
        assertTrue(shouldHandleSystemBack(Route.Settings, hasHistory = true, overlayOpen = false))
    }

    @Test
    fun `open overlay consumes back before page navigation`() {
        assertTrue(shouldHandleSystemBack(Route.EmployeeChat(9L), hasHistory = true, overlayOpen = true))
    }

    @Test
    fun `login root is not intercepted`() {
        assertFalse(shouldHandleSystemBack(Route.Login, hasHistory = false, overlayOpen = false))
        assertEquals(Route.Login, fallbackBackTarget(Route.Login))
    }
}
