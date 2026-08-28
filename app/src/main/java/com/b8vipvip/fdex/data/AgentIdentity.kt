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

/**
 * Compatibility wrapper around the historical project API. It preserves the stored autoOperation
 * flag and group behavior without creating company-oriented descriptions or system messages.
 */
fun AppRepository.createGeneralProject(
    title: String,
    description: String,
    professionalLevel: String,
    storageMode: String,
    retentionPolicy: String,
    allowAi: Boolean,
    autoDesensitize: Boolean,
    startAuto: Boolean,
): Project {
    val created = createProject(
        title = title,
        description = description,
        professionalLevel = professionalLevel,
        storageMode = storageMode,
        retentionPolicy = retentionPolicy,
        allowAi = allowAi,
        autoDesensitize = autoDesensitize,
        startAuto = false,
    )
    if (!startAuto) return created

    val group = createGroup(
        name = "${created.title} · 工作群",
        description = "自动协作工作群",
        projectId = created.id,
        memberIds = employees().map { it.id },
        autoMode = true,
    )
    addGroupMessage(group.id, "system", "", "自动协作已启动，当前智体可以围绕这项工作共同推进。")
    return created.copy(autoOperation = true).also(::updateProject)
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
