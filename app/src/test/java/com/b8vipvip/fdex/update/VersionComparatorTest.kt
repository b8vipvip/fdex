package com.b8vipvip.fdex.update

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VersionComparatorTest {
    @Test
    fun newerSemanticVersionIsDetected() {
        assertTrue(VersionComparator.isNewer("v1.2.0", "1.1.9"))
        assertTrue(VersionComparator.isNewer("2.0.0", "1.99.99"))
    }

    @Test
    fun sameOrOlderVersionIsIgnored() {
        assertFalse(VersionComparator.isNewer("1.0.0", "1.0.0"))
        assertFalse(VersionComparator.isNewer("0.9.9", "1.0.0"))
    }
}
