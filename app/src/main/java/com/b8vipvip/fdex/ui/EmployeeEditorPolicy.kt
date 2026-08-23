package com.b8vipvip.fdex.ui

internal fun canSaveEmployeeEditor(
    isEditing: Boolean,
    name: String,
    department: String,
    position: String,
    prompt: String,
    generating: Boolean,
): Boolean {
    if (generating) return false
    if (isEditing) return true
    return name.isNotBlank() &&
        department.isNotBlank() &&
        position.isNotBlank() &&
        prompt.isNotBlank()
}
