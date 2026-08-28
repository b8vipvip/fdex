package com.b8vipvip.fdex.data

const val PRIVATE_ASSISTANT_NAME = "私人助理"
const val PRIVATE_ASSISTANT_POSITION = "私人助理" // legacy compatibility only

fun Employee.isPrivateAssistant(): Boolean = name == PRIVATE_ASSISTANT_NAME

fun AppRepository.ensurePrivateAssistant(): Employee {
    val existing = employees(activeOnly = false).firstOrNull { it.isPrivateAssistant() }
    if (existing != null) return existing
    return addEmployee(
        name = PRIVATE_ASSISTANT_NAME,
        department = "",
        position = "",
        prompt = "",
        industry = "",
    )
}
