package com.b8vipvip.fdex.ui

import java.nio.file.Files
import java.nio.file.Path
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test


class GitHubWebPortalStructureTest {
    @Test
    fun `Android delegates GitHub authorization to the FDEX user web portal`() {
        val api = source("network", "AgentApi.kt")
        val setup = source("ui", "GitHubProjectSetup.kt")
        val center = source("ui", "AgentCenterScreen.kt")
        val chat = source("ui", "CodingAgentChatScreen.kt")

        // Phase 7.5 Device OAuth APIs remain server/client-compatible for old releases,
        // but the current Android UI must not start or poll that credential flow.
        assertTrue(api.contains("/api/agent/github/oauth/device/start"))
        assertTrue(api.contains("/api/agent/github/repositories"))
        assertTrue(api.contains("pollGitHubDeviceFlow"))
        assertFalse(api.contains("put(\"token\""))
        assertFalse(api.contains("saveGitHubConnection"))

        assertTrue(setup.contains("/account/github"))
        assertTrue(setup.contains("Android 不再要求输入"))
        assertTrue(setup.contains("access token / refresh token"))
        assertFalse(setup.contains("startGitHubDeviceFlow"))
        assertFalse(setup.contains("pollGitHubDeviceFlow"))
        assertFalse(setup.contains("OutlinedTextField"))
        assertTrue(center.contains("GitHubProjectSetup"))
        assertTrue(chat.contains("GitHubProjectSetup"))
        assertFalse(center.contains("PasswordVisualTransformation"))
        assertFalse(chat.contains("PasswordVisualTransformation"))
    }

    private fun source(packageName: String, fileName: String): String {
        val relative = Path.of("src", "main", "java", "com", "b8vipvip", "fdex", packageName, fileName)
        val candidates = listOf(relative, Path.of("app").resolve(relative))
        val path = candidates.firstOrNull { Files.exists(it) }
            ?: error("Unable to locate Android source $fileName from ${Path.of("").toAbsolutePath()}")
        return String(Files.readAllBytes(path), Charsets.UTF_8)
    }
}
