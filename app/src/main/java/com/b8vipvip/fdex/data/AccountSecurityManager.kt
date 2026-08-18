package com.b8vipvip.fdex.data

import android.content.Context
import java.security.MessageDigest

/** Password changes stay local and reuse the device-bound credential format used by login. */
internal class AccountSecurityManager(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("fdex_app_v2", Context.MODE_PRIVATE)
    private val credentials = LocalCredentialStore()

    fun changePassword(currentPassword: String, newPassword: String, confirmPassword: String): Result<Unit> {
        if (currentPassword.isBlank()) return Result.failure(IllegalArgumentException("请输入当前密码"))
        if (newPassword.length < 8) return Result.failure(IllegalArgumentException("新密码至少 8 位"))
        if (newPassword != confirmPassword) return Result.failure(IllegalArgumentException("两次输入的新密码不一致"))
        if (currentPassword == newPassword) return Result.failure(IllegalArgumentException("新密码不能与当前密码相同"))

        val verified = runCatching { verifyCurrent(currentPassword) }.getOrDefault(false)
        if (!verified) return Result.failure(IllegalArgumentException("当前密码不正确"))

        return runCatching {
            val record = credentials.createRecord(newPassword)
            prefs.edit()
                .putString(LocalCredentialStore.PREF_PASSWORD_RECORD, record)
                .remove(LocalCredentialStore.LEGACY_PASSWORD_HASH)
                .apply()
        }.map { Unit }
    }

    private fun verifyCurrent(password: String): Boolean {
        val modern = prefs.getString(LocalCredentialStore.PREF_PASSWORD_RECORD, "").orEmpty()
        if (modern.isNotBlank()) return credentials.verify(modern, password)
        val legacy = prefs.getString(LocalCredentialStore.LEGACY_PASSWORD_HASH, "").orEmpty()
        return legacy.isNotBlank() && MessageDigest.isEqual(
            legacy.toByteArray(Charsets.UTF_8),
            legacyHash(password).toByteArray(Charsets.UTF_8),
        )
    }

    private fun legacyHash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
