package com.b8vipvip.fdex.update

import android.content.Context

object UpdatePreferences {
    private const val PREFS_NAME = "fdex_update_preferences"
    private const val KEY_LAST_CHECK_AT = "last_check_at"
    private const val KEY_AUTO_CHECK = "auto_check"
    private const val CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000L

    fun shouldCheckOnLaunch(context: Context, now: Long = System.currentTimeMillis()): Boolean {
        if (!automaticCheckEnabled(context)) return false
        val lastCheckedAt = lastCheckAt(context)
        return now - lastCheckedAt >= CHECK_INTERVAL_MS
    }

    fun automaticCheckEnabled(context: Context): Boolean = context
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getBoolean(KEY_AUTO_CHECK, true)

    fun setAutomaticCheckEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_AUTO_CHECK, enabled)
            .apply()
    }

    fun lastCheckAt(context: Context): Long = context
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getLong(KEY_LAST_CHECK_AT, 0L)

    fun recordCheck(context: Context, now: Long = System.currentTimeMillis()) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_LAST_CHECK_AT, now)
            .apply()
    }
}
