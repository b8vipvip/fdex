package com.b8vipvip.fdex.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.KeyGenerator
import javax.crypto.Mac
import javax.crypto.SecretKey

/**
 * Versioned local password verifier.
 *
 * The password is first stretched with PBKDF2-HMAC-SHA256 and a random salt.
 * The derived value is then authenticated with a non-exportable Android
 * Keystore HMAC key (device-bound pepper). No plaintext password or reusable
 * SHA-256(password) value is stored.
 */
internal class LocalCredentialStore {
    fun createRecord(password: String): String {
        val salt = ByteArray(SALT_BYTES).also(SecureRandom()::nextBytes)
        val derived = PasswordKdf.derive(password.toCharArray(), salt)
        val verifier = hmac(derived)
        return listOf(
            RECORD_VERSION,
            PasswordKdf.DEFAULT_ITERATIONS.toString(),
            Base64.getEncoder().withoutPadding().encodeToString(salt),
            Base64.getEncoder().withoutPadding().encodeToString(verifier),
        ).joinToString("$")
    }

    fun verify(record: String, password: String): Boolean {
        val parts = record.split('$')
        if (parts.size != 4 || parts[0] != RECORD_VERSION) return false
        val iterations = parts[1].toIntOrNull()?.takeIf { it >= 100_000 } ?: return false
        val salt = runCatching { Base64.getDecoder().decode(parts[2]) }.getOrNull() ?: return false
        val expected = runCatching { Base64.getDecoder().decode(parts[3]) }.getOrNull() ?: return false
        if (salt.size < 16 || expected.isEmpty()) return false
        return runCatching {
            val derived = PasswordKdf.derive(password.toCharArray(), salt, iterations)
            MessageDigest.isEqual(expected, hmac(derived))
        }.getOrDefault(false)
    }

    fun deleteDeviceKey() {
        runCatching {
            val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
            if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
        }
    }

    private fun hmac(value: ByteArray): ByteArray {
        val mac = Mac.getInstance(KeyProperties.KEY_ALGORITHM_HMAC_SHA256)
        mac.init(getOrCreateDeviceKey())
        return mac.doFinal(value)
    }

    private fun getOrCreateDeviceKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_HMAC_SHA256, KEYSTORE_PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY,
            )
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        const val PREF_PASSWORD_RECORD = "account_password_v2"
        const val LEGACY_PASSWORD_HASH = "account_password"
        private const val RECORD_VERSION = "v2"
        private const val SALT_BYTES = 16
        private const val KEY_ALIAS = "fdex_local_auth_pepper_v1"
        private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
    }
}
