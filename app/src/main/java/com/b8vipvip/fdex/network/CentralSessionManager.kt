package com.b8vipvip.fdex.network

import android.content.Context
import com.b8vipvip.fdex.data.CentralSessionStore
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Serializes FDEX Center refresh-token rotation for the whole Android process.
 *
 * Refresh tokens rotate on every successful refresh. Without a single mutex, two requests
 * that notice the same expired access token could submit the same refresh token at once;
 * one succeeds and the other sees an already-rotated token. This manager always re-reads
 * the persisted session inside the lock and lets concurrent callers reuse the newest token.
 */
object CentralSessionManager {
    private const val PROACTIVE_REFRESH_SECONDS = 5 * 60L
    private val refreshMutex = Mutex()

    suspend fun ensureAccess(context: Context): String? {
        val store = CentralSessionStore(context)
        val current = store.accessToken().trim()
        if (current.isBlank()) return null
        if (!store.accessExpiresWithin(PROACTIVE_REFRESH_SECONDS)) return current
        return refresh(context, attemptedAccessToken = null, force = false)
    }

    suspend fun refreshAfterUnauthorized(context: Context, attemptedAccessToken: String): String? =
        refresh(context, attemptedAccessToken = attemptedAccessToken.trim(), force = true)

    private suspend fun refresh(
        context: Context,
        attemptedAccessToken: String?,
        force: Boolean,
    ): String? = refreshMutex.withLock {
        val store = CentralSessionStore(context)
        val latestAccess = store.accessToken().trim()
        if (latestAccess.isBlank()) return@withLock null

        // Another request may already have rotated the session while this caller waited.
        if (!attemptedAccessToken.isNullOrBlank() && latestAccess != attemptedAccessToken) {
            return@withLock latestAccess
        }
        if (!force && !store.accessExpiresWithin(PROACTIVE_REFRESH_SECONDS)) {
            return@withLock latestAccess
        }

        val refreshToken = store.refreshToken().trim()
        if (refreshToken.isBlank()) return@withLock latestAccess.takeUnless { store.accessExpired() }
        when (val result = CentralAuthApi.refresh(refreshToken)) {
            is CentralAuthResult.Success -> {
                store.save(result.value)
                result.value.accessToken.trim().takeIf { it.isNotBlank() }
            }
            is CentralAuthResult.Failure -> latestAccess.takeUnless { store.accessExpired() || force }
        }
    }
}
