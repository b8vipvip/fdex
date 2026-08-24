package com.b8vipvip.fdex.data

import android.content.Context

/** Local-only Coding Agent UI state. GitHub and AI provider secrets stay on the FDEX server. */
class AgentEmployeePreferences(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun isCodingAgent(employeeId: Long): Boolean = prefs.getBoolean(employeeKey(employeeId), false)

    fun setCodingAgent(employeeId: Long, enabled: Boolean) {
        prefs.edit().putBoolean(employeeKey(employeeId), enabled).apply()
    }

    fun accessToken(): String = prefs.getString(KEY_ACCESS_TOKEN, "").orEmpty()

    fun setAccessToken(value: String) {
        prefs.edit().putString(KEY_ACCESS_TOKEN, value.trim()).apply()
    }

    fun projectId(employeeId: Long): Int? {
        val value = prefs.getInt(projectKey(employeeId), 0)
        return value.takeIf { it > 0 }
    }

    fun setProjectId(employeeId: Long, projectId: Int?) {
        if (projectId == null) prefs.edit().remove(projectKey(employeeId)).apply()
        else prefs.edit().putInt(projectKey(employeeId), projectId).apply()
    }

    private fun employeeKey(employeeId: Long): String = "coding_agent_employee_$employeeId"
    private fun projectKey(employeeId: Long): String = "coding_agent_project_$employeeId"

    companion object {
        // Keep the v1 storage name so existing employee flags and Agent token survive upgrades.
        private const val PREFS_NAME = "fdex_agent_preferences_v1"
        private const val KEY_ACCESS_TOKEN = "agent_access_token"
    }
}
