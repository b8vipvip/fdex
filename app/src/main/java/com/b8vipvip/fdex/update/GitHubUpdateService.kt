package com.b8vipvip.fdex.update

import com.b8vipvip.fdex.BuildConfig
import java.net.HttpURLConnection
import java.net.URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

object GitHubUpdateService {
    private const val CONNECT_TIMEOUT_MS = 10_000
    private const val READ_TIMEOUT_MS = 15_000

    suspend fun checkForUpdate(currentVersion: String): UpdateCheckResult = withContext(Dispatchers.IO) {
        runCatching {
            val endpoint = "https://api.github.com/repos/${BuildConfig.GITHUB_OWNER}/${BuildConfig.GITHUB_REPO}/releases/latest"
            val connection = (URL(endpoint).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                setRequestProperty("Accept", "application/vnd.github+json")
                setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
                setRequestProperty("User-Agent", "FDEX-Android/${BuildConfig.VERSION_NAME}")
            }

            try {
                val responseCode = connection.responseCode
                if (responseCode == HttpURLConnection.HTTP_NOT_FOUND) {
                    return@runCatching UpdateCheckResult.UpToDate
                }
                if (responseCode !in 200..299) {
                    val error = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                    error("GitHub 返回 HTTP $responseCode${if (error.isBlank()) "" else "：${error.take(160)}"}")
                }

                val json = connection.inputStream.bufferedReader().use { JSONObject(it.readText()) }
                val assets = json.optJSONArray("assets")
                var apkUrl: String? = null
                if (assets != null) {
                    for (index in 0 until assets.length()) {
                        val asset = assets.optJSONObject(index) ?: continue
                        val name = asset.optString("name")
                        if (name.endsWith(".apk", ignoreCase = true)) {
                            apkUrl = asset.optString("browser_download_url").takeIf(String::isNotBlank)
                            break
                        }
                    }
                }

                val release = ReleaseInfo(
                    tagName = json.optString("tag_name"),
                    name = json.optString("name").ifBlank { json.optString("tag_name") },
                    body = json.optString("body"),
                    htmlUrl = json.optString("html_url"),
                    apkUrl = apkUrl,
                    publishedAt = json.optString("published_at"),
                )

                if (release.tagName.isBlank()) {
                    UpdateCheckResult.Failed("GitHub Release 缺少版本标签")
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
