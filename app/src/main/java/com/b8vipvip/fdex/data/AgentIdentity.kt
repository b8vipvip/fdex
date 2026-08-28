package com.b8vipvip.fdex.data

/**
 * User-facing 智体 identity layer.
 *
 * The legacy Employee data class and JSON keys stay intact so existing per-user databases, memory
 * ACLs, group member ids and Coding Agent preferences keep working. New records deliberately leave
 * the old company taxonomy fields blank.
 */
fun AppRepository.addAgent(identityPrompt: String = ""): Employee {
    val names = employees(activeOnly = false).map { it.name.trim() }.toSet()
    var index = 1
    while ("智体 $index" in names) index++
    return addEmployee(
        name = "智体 $index",
        department = "",
        position = "",
        prompt = identityPrompt.trim(),
        industry = "",
    )
}

fun AppRepository.scrubLegacyBusinessMetadata() {
    val current = profile()
    if (current.companyName.isNotBlank() || current.industry.isNotBlank() || current.autoCompanyMode) {
        updateProfile(current.copy(companyName = "", industry = "", autoCompanyMode = false))
    }
    employees(activeOnly = false).forEach { agent ->
        if (agent.department.isNotBlank() || agent.position.isNotBlank() || agent.industry.isNotBlank()) {
            updateEmployee(agent.copy(department = "", position = "", industry = ""))
        }
    }
}
