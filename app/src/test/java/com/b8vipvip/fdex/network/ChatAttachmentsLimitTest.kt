package com.b8vipvip.fdex.network

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatAttachmentsLimitTest {
    @Test
    fun cameraPhotosLargerThanOldEightMegabyteLimitCanBePreprocessed() {
        val twentyMegabytes = 20L * 1024L * 1024L
        assertTrue(imageSourceSizeAllowed(twentyMegabytes))
    }

    @Test
    fun pathologicalHugeSourceIsStillRejectedBeforeDecode() {
        val overLimit = MAX_IMAGE_SOURCE_BYTES.toLong() + 1L
        assertFalse(imageSourceSizeAllowed(overLimit))
    }

    @Test
    fun unknownProviderSizeIsAllowedToProceedToStreamDecode() {
        assertTrue(imageSourceSizeAllowed(-1L))
    }

    @Test
    fun finalVisionPayloadBudgetIsFarBelowRawSourceBudget() {
        assertTrue(MAX_IMAGE_PAYLOAD_BYTES < MAX_IMAGE_SOURCE_BYTES)
        assertTrue(MAX_IMAGE_PAYLOAD_BYTES <= 2 * 1024 * 1024)
    }
}
