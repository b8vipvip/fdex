package com.b8vipvip.fdex.network

import android.os.Build
import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit


data class CentralUserDto(
    val id: String,
    val email: String,
    val name: String,
    val companyName: String,
)

data class CentralSessionDto(
    val user: CentralUserDto,
    val accessToken: String,
    val refreshToken: String,
    val accessExpiresAt: String,
    val refreshExpiresAt: String,
)

sealed interface CentralAuthResult<out T> {
    data class Success<T>(val value: T) : CentralAuthResult<T>
    data class Failure(val message: String) : CentralAuthResult<Nothing>
}

object CentralAuthApi {
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val client = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun register(name: String, email: String, password: String, companyName: String): CentralAuthResult<CentralSessionDto> =
        authRequest(
            "/api/auth/register",
            JSONObject()
                .put("name", name.trim())
                .put("email", email.trim())
                .put("password", password)
                .put("company_name", companyName.trim())
                .put("device_name", "Android ${Build.MODEL}"),
        )

    suspend fun login(email: String, password: String): CentralAuthResult<CentralSessionDto> =
        authRequest(
            "/api/auth/login",
            JSONObject()
                .put("email", email.trim())
                .put("password", password)
                .put("device_name", "Android ${Build.MODEL}"),
        )

    suspend fun refresh(refreshToken: String): CentralAuthResult<CentralSessionDto> =
        authRequest("/api/auth/refresh", JSONObject().put("refresh_token", refreshToken.trim()))

    suspend fun logout(accessToken: String): CentralAuthResult<Boolean> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("${BuildConfig.SERVER_BASE_URL}/api/auth/logout")
                .header("Authorization", "Bearer ${accessToken.trim()}")
                .post(JSONObject().toString().toRequestBody(jsonType))
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) CentralAuthResult.Success(true)
                else CentralAuthResult.Failure(errorMessage(response.body?.string().orEmpty(), "退出登录失败"))
            }
        } catch (error: Exception) {
            CentralAuthResult.Failure(error.message ?: "无法连接 FDEX 中心服务器")
        }
    }

    private suspend fun authRequest(path: String, payload: JSONObject): CentralAuthResult<CentralSessionDto> = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("${BuildConfig.SERVER_BASE_URL}$path")
                .header("Accept", "application/json")
                .post(payload.toString().toRequestBody(jsonType))
                .build()
            client.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) return@withContext CentralAuthResult.Failure(errorMessage(body, "FDEX 登录服务 HTTP ${response.code}"))
                runCatching { parseSession(JSONObject(body)) }
                    .fold({ CentralAuthResult.Success(it) }, { CentralAuthResult.Failure("FDEX 登录响应格式无效") })
            }
        } catch (error: Exception) {
            CentralAuthResult.Failure(error.message ?: "无法连接 FDEX 中心服务器")
        }
    }

    internal fun parseSession(json: JSONObject): CentralSessionDto {
        val user = json.getJSONObject("user")
        return CentralSessionDto(
            user = CentralUserDto(
                id = user.getString("id"),
                email = user.getString("email"),
                name = user.getString("name"),
                companyName = user.optString("company_name"),
            ),
            accessToken = json.getString("access_token"),
            refreshToken = json.getString("refresh_token"),
            accessExpiresAt = json.optString("access_expires_at"),
            refreshExpiresAt = json.optString("refresh_expires_at"),
        )
    }

    private fun errorMessage(body: String, fallback: String): String = runCatching {
        JSONObject(body).optString("detail")
    }.getOrNull().orEmpty().ifBlank { fallback }
}
