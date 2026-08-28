package com.b8vipvip.fdex.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EmployeeEditorPolicyTest {
    @Test
    fun existingAgentCanSavePermissionOnlyWithBlankPrompt() {
        assertTrue(canSaveEmployeeEditor(true, "", "", "", "", false))
    }

    @Test
    fun newAgentCanBeCreatedWithOnlyOptionalIdentityPromptOrBlank() {
        assertTrue(canSaveEmployeeEditor(false, "", "", "", "", false))
        assertTrue(canSaveEmployeeEditor(false, "", "", "", "你是我的语文老师", false))
    }

    @Test
    fun generatingDisablesSaveForBothModes() {
        assertFalse(canSaveEmployeeEditor(true, "", "", "", "", true))
        assertFalse(canSaveEmployeeEditor(false, "", "", "", "", true))
    }
}
