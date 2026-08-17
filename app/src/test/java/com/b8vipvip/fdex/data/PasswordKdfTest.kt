package com.b8vipvip.fdex.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest

class PasswordKdfTest {
    @Test
    fun samePasswordAndSaltProducesSameVerifier() {
        val salt = ByteArray(16) { it.toByte() }
        val first = PasswordKdf.derive("correct horse battery staple".toCharArray(), salt, 100_000)
        val second = PasswordKdf.derive("correct horse battery staple".toCharArray(), salt, 100_000)
        assertTrue(MessageDigest.isEqual(first, second))
    }

    @Test
    fun changingSaltChangesDerivedValue() {
        val first = PasswordKdf.derive("same-password".toCharArray(), ByteArray(16) { 1 }, 100_000)
        val second = PasswordKdf.derive("same-password".toCharArray(), ByteArray(16) { 2 }, 100_000)
        assertFalse(MessageDigest.isEqual(first, second))
    }
}
