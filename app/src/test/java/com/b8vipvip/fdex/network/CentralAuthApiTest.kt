package com.b8vipvip.fdex.network

import java.nio.file.Files
import java.nio.file.Path
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CentralAuthApiTest {
    @Test
    fun centralAuthClientUsesOpaqueCenterSessions() {
        val source = mainSource("CentralAuthApi.kt")
        assertTrue(source.contains("/api/auth/register"))
        assertTrue(source.contains("/api/auth/login"))
        assertTrue(source.contains("/api/auth/refresh"))
        assertTrue(source.contains("/api/auth/logout"))
        assertTrue(source.contains("access_token"))
        assertTrue(source.contains("refresh_token"))
        assertFalse(source.contains("putString(\"password\""))
    }

    @Test
    fun codingAgentUsesCentralBearerIdentity() {
        val source = mainSource("AgentApi.kt")
        assertTrue(source.contains("Authorization"))
        assertTrue(source.contains("Bearer ${'$'}{accessToken.trim()}"))
        assertFalse(source.contains("X-FDEX-Account-Token"))
    }

    private fun mainSource(fileName: String): String {
        val relative = Path.of("src", "main", "java", "com", "b8vipvip", "fdex", "network", fileName)
        val candidates = listOf(relative, Path.of("app").resolve(relative))
        val path = candidates.firstOrNull { Files.exists(it) }
            ?: error("Unable to locate Android network source $fileName from ${Path.of("").toAbsolutePath()}")
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
