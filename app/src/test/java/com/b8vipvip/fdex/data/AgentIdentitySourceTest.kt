package com.b8vipvip.fdex.data

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AgentIdentitySourceTest {
    @Test
    fun newAgentIdentityUsesOnlyOptionalPromptAndBlankLegacyTaxonomy() {
        val source = File("src/main/java/com/b8vipvip/fdex/data/AgentIdentity.kt").readText()
        assertTrue(source.contains("fun AppRepository.addAgent(identityPrompt: String = \"\")"))
        assertTrue(source.contains("name = \"智体 $index\""))
        assertTrue(source.contains("department = \"\""))
        assertTrue(source.contains("position = \"\""))
        assertTrue(source.contains("industry = \"\""))
    }
}
