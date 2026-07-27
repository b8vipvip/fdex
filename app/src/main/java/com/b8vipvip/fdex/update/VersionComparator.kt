package com.b8vipvip.fdex.update

object VersionComparator {
    fun isNewer(candidate: String, current: String): Boolean {
        val candidateParts = numericParts(candidate)
        val currentParts = numericParts(current)
        val size = maxOf(candidateParts.size, currentParts.size)

        repeat(size) { index ->
            val left = candidateParts.getOrElse(index) { 0 }
            val right = currentParts.getOrElse(index) { 0 }
            if (left != right) return left > right
        }
        return false
    }

    private fun numericParts(version: String): List<Int> = version
        .trim()
        .removePrefix("v")
        .removePrefix("V")
        .substringBefore('-')
        .split('.')
        .map { part -> part.takeWhile(Char::isDigit).toIntOrNull() ?: 0 }
}
