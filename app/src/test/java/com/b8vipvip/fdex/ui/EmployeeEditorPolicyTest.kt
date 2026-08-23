package com.b8vipvip.fdex.ui

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EmployeeEditorPolicyTest {
    @Test
    fun existingEmployeeCanSavePermissionOnlyEvenWithBlankPrompt() {
        assertTrue(
            canSaveEmployeeEditor(
                isEditing = true,
                name = "小策",
                department = "经营中心",
                position = "业务策划",
                prompt = "",
                generating = false,
            ),
        )
    }

    @Test
    fun newEmployeeStillRequiresCompleteBaseFieldsAndPrompt() {
        assertFalse(canSaveEmployeeEditor(false, "小策", "经营中心", "业务策划", "", false))
        assertTrue(canSaveEmployeeEditor(false, "小策", "经营中心", "业务策划", "完整 Prompt", false))
    }

    @Test
    fun generatingDisablesSaveForBothModes() {
        assertFalse(canSaveEmployeeEditor(true, "", "", "", "", true))
        assertFalse(canSaveEmployeeEditor(false, "小策", "经营中心", "业务策划", "Prompt", true))
    }
}
