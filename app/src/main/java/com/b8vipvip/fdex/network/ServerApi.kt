package com.b8vipvip.fdex.network

import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

sealed interface ServerCheckResult {
    data class Online(val service: String, val version: String) : ServerCheckResult
    data class Offline(val message: String) : ServerCheckResult
}

object ServerApi {
    suspend fun checkHealth(): ServerCheckResult = withContext(Dispatchers.IO) {
        val connection = runCatching {
            URL("${BuildConfig.SERVER_BASE_URL}/api/health").openConnection() as HttpURLConnection
        }.getOrElse { return@withContext ServerCheckResult.Offline("服务地址无效") }

        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 8_000
            connection.readTimeout = 8_000
            connection.setRequestProperty("Accept", "application/json")

            if (connection.responseCode !in 200..299) {
                return@withContext ServerCheckResult.Offline("服务端返回 HTTP ${connection.responseCode}")
            }

            val body = connection.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            ServerCheckResult.Online(
                service = json.optString("service", "FDEX Server"),
                version = json.optString("version", "unknown"),
            )
        } catch (error: Exception) {
            ServerCheckResult.Offline(error.message ?: "无法连接服务端")
        } finally {
            connection.disconnect()
        }
    }
}
