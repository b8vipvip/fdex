package com.b8vipvip.fdex.data

import android.content.Context

/** User-controlled client/privacy preferences that must have real product effects. */
class ClientPreferences(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun phone(): String = prefs.getString(KEY_PHONE, "").orEmpty()
    fun setPhone(value: String) = prefs.edit().putString(KEY_PHONE, value.trim()).apply()

    fun autoArchiveKnowledge(): Boolean = prefs.getBoolean(KEY_AUTO_ARCHIVE_KNOWLEDGE, true)
    fun setAutoArchiveKnowledge(value: Boolean) = prefs.edit().putBoolean(KEY_AUTO_ARCHIVE_KNOWLEDGE, value).apply()

    fun remoteLongTermMemory(): Boolean = prefs.getBoolean(KEY_REMOTE_LONG_TERM_MEMORY, true)
    fun setRemoteLongTermMemory(value: Boolean) = prefs.edit().putBoolean(KEY_REMOTE_LONG_TERM_MEMORY, value).apply()

    fun defaultHome(): String = prefs.getString(KEY_DEFAULT_HOME, HOME_MESSAGES)
        .orEmpty()
        .takeIf { it in HOME_VALUES }
        ?: HOME_MESSAGES
    fun setDefaultHome(value: String) {
        prefs.edit().putString(KEY_DEFAULT_HOME, value.takeIf { it in HOME_VALUES } ?: HOME_MESSAGES).apply()
    }

    fun showReasoning(): Boolean = prefs.getBoolean(KEY_SHOW_REASONING, true)
    fun setShowReasoning(value: Boolean) = prefs.edit().putBoolean(KEY_SHOW_REASONING, value).apply()

    fun autoScrollChat(): Boolean = prefs.getBoolean(KEY_AUTO_SCROLL_CHAT, true)
    fun setAutoScrollChat(value: Boolean) = prefs.edit().putBoolean(KEY_AUTO_SCROLL_CHAT, value).apply()

    companion object {
        const val HOME_MESSAGES = "messages"
        const val HOME_KNOWLEDGE = "knowledge"
        const val HOME_DISCOVER = "discover"
        const val HOME_ME = "me"
        val HOME_VALUES = setOf(HOME_MESSAGES, HOME_KNOWLEDGE, HOME_DISCOVER, HOME_ME)

        private const val PREFS_NAME = "fdex_client_preferences_v1"
        private const val KEY_PHONE = "profile_phone"
        private const val KEY_AUTO_ARCHIVE_KNOWLEDGE = "privacy_auto_archive_knowledge"
        private const val KEY_REMOTE_LONG_TERM_MEMORY = "privacy_remote_long_term_memory"
        private const val KEY_DEFAULT_HOME = "client_default_home"
        private const val KEY_SHOW_REASONING = "client_show_reasoning"
        private const val KEY_AUTO_SCROLL_CHAT = "client_auto_scroll_chat"
    }
}
