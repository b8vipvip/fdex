package com.b8vipvip.fdex.network

import android.content.Context
import android.os.Build
import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object ClientDiagnosticsApi {
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    suspend fun upload(context: Context, entries: List<JSONObject>): Boolean = withContext(Dispatchers.IO) {
        if (entries.isEmpty()) return@withContext true
        val initialToken = CentralSessionManager.ensureAccess(context).orEmpty()
        if (initialToken.isBlank()) return@withContext false
        val payload = JSONObject()
            .put("device_name", "Android ${Build.MANUFACTURER} ${Build.MODEL}".trim())
            .put("platform", "android")
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("git_sha", BuildConfig.GIT_SHA)
            .put("os_version", "Android ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
            .put("entries", JSONArray().apply { entries.take(50).forEach(::put) })

        fun request(token: String): Request = Request.Builder()
            .url("${BuildConfig.SERVER_BASE_URL}/api/client-logs/batch")
            .header("Accept", "application/json")
            .header("Authorization", "Bearer ${token.trim()}")
            .post(payload.toString().toRequestBody(jsonType))
            .build()

        try {
            var token = initialToken
            repeat(2) { attempt ->
                client.newCall(request(token)).execute().use { response ->
                    if (response.isSuccessful) return@withContext true
                    if (response.code == 401 && attempt == 0) {
                        token = CentralSessionManager.refreshAfterUnauthorized(context, token).orEmpty()
                        if (token.isBlank()) return@withContext false
                    } else {
                        return@withContext false
                    }
                }
            }
            false
        } catch (_: Exception) {
            false
        }
    }
}
