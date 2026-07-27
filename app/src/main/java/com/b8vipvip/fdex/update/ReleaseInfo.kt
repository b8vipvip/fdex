package com.b8vipvip.fdex.update

data class ReleaseInfo(
    val tagName: String,
    val name: String,
    val body: String,
    val htmlUrl: String,
    val apkUrl: String?,
    val publishedAt: String,
) {
    val normalizedVersion: String
        get() = tagName.trim().removePrefix("v").removePrefix("V")
}

sealed interface UpdateCheckResult {
    data class UpdateAvailable(val release: ReleaseInfo) : UpdateCheckResult
    data object UpToDate : UpdateCheckResult
    data class Failed(val message: String) : UpdateCheckResult
}
