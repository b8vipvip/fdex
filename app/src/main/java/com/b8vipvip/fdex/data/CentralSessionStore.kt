package com.b8vipvip.fdex.data

import android.content.Context
import com.b8vipvip.fdex.network.CentralSessionDto

/** Stores only FDEX Center session material. Passwords are never persisted on Android. */
class CentralSessionStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun isLoggedIn(): Boolean = userId().isNotBlank() && accessToken().isNotBlank() && refreshToken().isNotBlank()
    fun userId(): String = prefs.getString(KEY_USER_ID, "").orEmpty()
    fun accessToken(): String = prefs.getString(KEY_ACCESS_TOKEN, "").orEmpty()
    fun refreshToken(): String = prefs.getString(KEY_REFRESH_TOKEN, "").orEmpty()
    fun email(): String = prefs.getString(KEY_EMAIL, "").orEmpty()
    fun name(): String = prefs.getString(KEY_NAME, "").orEmpty()
    fun companyName(): String = prefs.getString(KEY_COMPANY, "").orEmpty()

    fun save(session: CentralSessionDto) {
        prefs.edit()
            .putString(KEY_USER_ID, session.user.id)
            .putString(KEY_EMAIL, session.user.email)
            .putString(KEY_NAME, session.user.name)
            .putString(KEY_COMPANY, session.user.companyName)
            .putString(KEY_ACCESS_TOKEN, session.accessToken)
            .putString(KEY_REFRESH_TOKEN, session.refreshToken)
            .putString(KEY_ACCESS_EXPIRES, session.accessExpiresAt)
            .putString(KEY_REFRESH_EXPIRES, session.refreshExpiresAt)
            .apply()
    }

    fun clear() = prefs.edit().clear().apply()

    companion object {
        const val PREFS = "fdex_central_session_v1"
        const val KEY_USER_ID = "user_id"
        private const val KEY_EMAIL = "email"
        private const val KEY_NAME = "name"
        private const val KEY_COMPANY = "company"
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_ACCESS_EXPIRES = "access_expires_at"
        private const val KEY_REFRESH_EXPIRES = "refresh_expires_at"
    }
}
