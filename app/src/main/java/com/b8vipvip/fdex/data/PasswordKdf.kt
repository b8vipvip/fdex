package com.b8vipvip.fdex.data

import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

internal object PasswordKdf {
    const val DEFAULT_ITERATIONS = 210_000
    private const val KEY_BITS = 256

    fun derive(password: CharArray, salt: ByteArray, iterations: Int = DEFAULT_ITERATIONS): ByteArray {
        require(iterations >= 100_000) { "PBKDF2 iterations too low" }
        val spec = PBEKeySpec(password, salt, iterations, KEY_BITS)
        return try {
            SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }
}
