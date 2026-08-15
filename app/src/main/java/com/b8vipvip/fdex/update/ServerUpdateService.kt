package com.b8vipvip.fdex.update

import com.b8vipvip.fdex.BuildConfig
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

object ServerUpdateService {
    private const val CONNECT_TIMEOUT_MS = 8_000
    private const val READ_TIMEOUT_MS = 12_000

    suspend fun checkForUpdate(currentVersion: String): UpdateCheckResult = withContext(Dispatchers.IO) {
        runCatching {
            val encodedVersion = URLEncoder.encode(currentVersion, Charsets.UTF_8.name())
            val endpoint = "${BuildConfig.SERVER_BASE_URL}/api/client/update?current_version=$encodedVersion"
            val connection = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                setRequestProperty("Accept", "application/json")
                setRequestProperty("User-Agent", "FDEX-Android/${BuildConfig.VERSION_NAME}")
            }

            try {
                val responseCode = connection.responseCode
                if (responseCode !in 200..299) {
                    val error = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    error("FDEX 服务端返回 HTTP $responseCode${if (error.isBlank()) "" else "：${error.take(160)}"}")
                }

                val json = connection.inputStream.bufferedReader().use { JSONObject(it.readText()) }
                val status = json.optString("status")
                if (status != "ready") {
                    return@runCatching UpdateCheckResult.Failed(
                        when (status) {
                            "waiting_for_server_cache" -> "服务端发现了更新，正在同步真正的最新版本 APK，请稍后重试"
                            "latest_version_unverified" -> "服务端正在确认 GitHub 当前最新版本，请稍后重试"
                            "cache_incomplete" -> "最新版本 APK 缓存尚未就绪，请稍后重试"
                            else -> "服务端更新服务暂未就绪"
                        },
                    )
                }

                if (!json.optBoolean("available", false)) {
                    return@runCatching UpdateCheckResult.UpToDate
                }

                val apkUrl = json.optString("apk_url").takeIf(String::isNotBlank)
                if (apkUrl == null) {
                    return@runCatching UpdateCheckResult.Failed("服务端已发现新版本，但最新 APK 下载地址尚未就绪")
                }

                val tagName = json.optString("tag_name")
                val latestVersion = json.optString("latest_version")
                val release = ReleaseInfo(
                    tagName = tagName.ifBlank { "v$latestVersion" },
                    name = json.optString("name").ifBlank { "FDEX $latestVersion" },
                    body = json.optString("body"),
                    htmlUrl = BuildConfig.SERVER_BASE_URL,
                    apkUrl = apkUrl,
                    publishedAt = json.optString("published_at"),
                    sha256 = json.optString("sha256"),
                    size = json.optLong("size", 0L),
                )

                if (release.tagName.isBlank()) {
                    UpdateCheckResult.Failed("服务端更新信息缺少版本号")
                } else if (VersionComparator.isNewer(release.normalizedVersion, currentVersion)) {
                    UpdateCheckResult.UpdateAvailable(release)
                } else {
                    UpdateCheckResult.UpToDate
                }
            } finally {
                connection.disconnect()
            }
        }.getOrElse { throwable ->
            UpdateCheckResult.Failed(throwable.message ?: "检查更新失败")
        }
    }
}
