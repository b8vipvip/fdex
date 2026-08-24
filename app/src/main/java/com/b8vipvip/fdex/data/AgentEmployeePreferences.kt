package com.b8vipvip.fdex.data

import android.content.Context

/** Local-only selection and credential state for Coding Agent employees. */
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

    private fun employeeKey(employeeId: Long): String = "coding_agent_employee_$employeeId"

    companion object {
        private const val PREFS_NAME = "fdex_agent_preferences_v1"
        private const val KEY_ACCESS_TOKEN = "agent_access_token"
    }
}
