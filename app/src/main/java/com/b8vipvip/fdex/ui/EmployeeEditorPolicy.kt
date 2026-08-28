package com.b8vipvip.fdex.ui

/**
 * Legacy parameters stay in the signature so older call sites and persisted editor flows remain
 * source-compatible during the 智体 terminology migration. New 智体 no longer require name,
 * department, position or prompt; the repository assigns a display name automatically and the
 * identity prompt may intentionally be blank.
 */
internal fun canSaveEmployeeEditor(
    isEditing: Boolean,
    name: String,
    department: String,
    position: String,
    prompt: String,
    generating: Boolean,
): Boolean {
    isEditing.hashCode()
    name.hashCode()
    department.hashCode()
    position.hashCode()
    prompt.hashCode()
    return !generating
}
