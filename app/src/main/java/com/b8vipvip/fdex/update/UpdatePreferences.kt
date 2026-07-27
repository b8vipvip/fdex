package com.b8vipvip.fdex.update

import android.content.Context

object UpdatePreferences {
    private const val PREFS_NAME = "fdex_update_preferences"
    private const val KEY_LAST_CHECK_AT = "last_check_at"
    private const val CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000L

    fun shouldCheckOnLaunch(context: Context, now: Long = System.currentTimeMillis()): Boolean {
        val lastCheckedAt = context
            .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getLong(KEY_LAST_CHECK_AT, 0L)
        return now - lastCheckedAt >= CHECK_INTERVAL_MS
    }

    fun recordCheck(context: Context, now: Long = System.currentTimeMillis()) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_LAST_CHECK_AT, now)
            .apply()
    }
}
