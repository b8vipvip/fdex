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

data class CentralDeviceSessionDto(
    val id: String,
    val deviceName: String,
    val clientIp: String,
    val createdAt: String,
    val lastSeenAt: String,
    val refreshExpiresAt: String,
    val active: Boolean,
    val current: Boolean,
)

data class CentralSecurityEventDto(
    val event: String,
    val success: Boolean,
    val risk: String,
    val clientIp: String,
    val deviceName: String,
    val createdAt: String,
)

data class CentralMemoryStatusDto(
    val phase: String,
    val memoryScopes: Int,
    val registeredDeviceScopes: Int,
    val mempalaceRows: Int,
    val qdrantPoints: Int,
    val lettaAgents: Int,
    val lastError: String,
    val updatedAt: String,
    val completedAt: String,
    val busy: Boolean,
    val operation: String,
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

    suspend fun requestPasswordReset(email: String): CentralAuthResult<String> = plainJsonRequest(
        "/api/auth/password/reset/request",
        JSONObject().put("email", email.trim()),
    ) { json -> json.optString("message").ifBlank { "如果邮箱已注册，验证码邮件将很快送达" } }

    suspend fun confirmPasswordReset(email: String, code: String, newPassword: String): CentralAuthResult<String> = plainJsonRequest(
        "/api/auth/password/reset/confirm",
        JSONObject().put("email", email.trim()).put("code", code.trim()).put("new_password", newPassword),
    ) { json -> json.optString("message").ifBlank { "密码已重置，请重新登录" } }

    suspend fun changePassword(context: Context, currentPassword: String, newPassword: String): CentralAuthResult<Boolean> =
        authorizedJsonRequest(
            context,
            "POST",
            "/api/auth/password/change",
            JSONObject().put("current_password", currentPassword).put("new_password", newPassword),
        ) { true }

    suspend fun listSessions(context: Context): CentralAuthResult<List<CentralDeviceSessionDto>> =
        authorizedJsonRequest(context, "GET", "/api/auth/sessions", null) { json ->
            val array = json.optJSONArray("sessions")
            buildList {
                if (array != null) for (index in 0 until array.length()) {
                    val item = array.optJSONObject(index) ?: continue
                    add(
                        CentralDeviceSessionDto(
                            id = item.optString("id"),
                            deviceName = item.optString("device_name"),
                            clientIp = item.optString("client_ip"),
                            createdAt = item.optString("created_at"),
                            lastSeenAt = item.optString("last_seen_at"),
                            refreshExpiresAt = item.optString("refresh_expires_at"),
                            active = item.optBoolean("active"),
                            current = item.optBoolean("current"),
                        ),
                    )
                }
            }
        }

    suspend fun revokeSession(context: Context, sessionId: String): CentralAuthResult<Boolean> =
        authorizedJsonRequest(context, "POST", "/api/auth/sessions/${sessionId.trim()}/revoke", JSONObject()) { true }

    suspend fun logoutAll(context: Context): CentralAuthResult<Boolean> =
        authorizedJsonRequest(context, "POST", "/api/auth/logout-all", JSONObject()) { true }

    suspend fun securityEvents(context: Context): CentralAuthResult<List<CentralSecurityEventDto>> =
        authorizedJsonRequest(context, "GET", "/api/auth/security-events?limit=30", null) { json ->
            val array = json.optJSONArray("events")
            buildList {
                if (array != null) for (index in 0 until array.length()) {
                    val item = array.optJSONObject(index) ?: continue
                    add(
                        CentralSecurityEventDto(
                            event = item.optString("event"),
                            success = item.optBoolean("success"),
                            risk = item.optString("risk"),
                            clientIp = item.optString("client_ip"),
                            deviceName = item.optString("device_name"),
                            createdAt = item.optString("created_at"),
                        ),
                    )
                }
            }
        }

    suspend fun registerMemoryScope(context: Context, localScopeToken: String): CentralAuthResult<Int> =
        authorizedJsonRequest(
            context,
            "POST",
            "/api/auth/memory/register-scope",
            JSONObject().put("scope_token", localScopeToken.trim()),
        ) { it.optInt("registered_scopes") }

    suspend fun memoryStatus(context: Context): CentralAuthResult<CentralMemoryStatusDto> =
        authorizedJsonRequest(context, "GET", "/api/auth/memory/status", null) { json ->
            val op = json.optJSONObject("operation") ?: JSONObject()
            CentralMemoryStatusDto(
                phase = json.optString("phase", "idle"),
                memoryScopes = json.optInt("memory_scopes"),
                registeredDeviceScopes = json.optInt("registered_device_scopes"),
                mempalaceRows = json.optInt("mempalace_rows"),
                qdrantPoints = json.optInt("qdrant_points"),
                lettaAgents = json.optInt("letta_agents"),
                lastError = json.optString("last_error"),
                updatedAt = json.optString("updated_at"),
                completedAt = json.optString("completed_at"),
                busy = op.optBoolean("busy"),
                operation = op.optString("operation"),
            )
        }

    suspend fun clearMemory(context: Context, password: String): CentralAuthResult<Boolean> =
        authorizedJsonRequest(
            context,
            "POST",
            "/api/auth/memory/clear",
            JSONObject().put("password", password).put("confirmation", "CLEAR MY FDEX MEMORY"),
        ) { true }

    suspend fun exportData(context: Context): CentralAuthResult<String> =
        authorizedJsonRequest(context, "GET", "/api/auth/data-export", null) { json -> json.toString(2) }

    suspend fun deleteAccount(context: Context, password: String): CentralAuthResult<Boolean> =
        authorizedJsonRequest(
            context,
            "POST",
            "/api/auth/account/delete",
            JSONObject().put("password", password).put("confirmation", "DELETE MY FDEX"),
        ) { true }

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

    private suspend fun <T> plainJsonRequest(path: String, payload: JSONObject, parser: (JSONObject) -> T): CentralAuthResult<T> =
        withContext(Dispatchers.IO) {
            try {
                val request = Request.Builder()
                    .url("${BuildConfig.SERVER_BASE_URL}$path")
                    .header("Accept", "application/json")
                    .post(payload.toString().toRequestBody(jsonType))
                    .build()
                client.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    if (!response.isSuccessful) return@withContext CentralAuthResult.Failure(errorMessage(body, "FDEX 账号服务 HTTP ${response.code}"))
                    runCatching { parser(JSONObject(body)) }
                        .fold({ CentralAuthResult.Success(it) }, { CentralAuthResult.Failure("FDEX 账号响应格式无效") })
                }
            } catch (error: Exception) {
                CentralAuthResult.Failure(error.message ?: "无法连接 FDEX 中心服务器")
            }
        }

    private suspend fun <T> authorizedJsonRequest(
        context: Context,
        method: String,
        path: String,
        payload: JSONObject?,
        parser: (JSONObject) -> T,
    ): CentralAuthResult<T> = withContext(Dispatchers.IO) {
        try {
            var token = CentralSessionManager.ensureAccess(context)
                ?: return@withContext CentralAuthResult.Failure("FDEX 登录状态已失效，请重新登录")
            repeat(2) { attempt ->
                val request = buildAuthorizedRequest(method, path, token, payload)
                client.newCall(request).execute().use { response ->
                    val body = response.body?.string().orEmpty()
                    if (response.code == 401 && attempt == 0) {
                        token = CentralSessionManager.refreshAfterUnauthorized(context, token)
                            ?: return@withContext CentralAuthResult.Failure("FDEX 登录状态已失效，请重新登录")
                    } else if (!response.isSuccessful) {
                        return@withContext CentralAuthResult.Failure(errorMessage(body, "FDEX 账号服务 HTTP ${response.code}"))
                    } else {
                        return@withContext runCatching { parser(JSONObject(body.ifBlank { "{}" })) }
                            .fold({ CentralAuthResult.Success(it) }, { CentralAuthResult.Failure("FDEX 账号响应格式无效") })
                    }
                }
            }
            CentralAuthResult.Failure("FDEX 登录状态已失效，请重新登录")
        } catch (error: Exception) {
            CentralAuthResult.Failure(error.message ?: "无法连接 FDEX 中心服务器")
        }
    }

    private fun buildAuthorizedRequest(method: String, path: String, token: String, payload: JSONObject?): Request {
        val builder = Request.Builder()
            .url("${BuildConfig.SERVER_BASE_URL}$path")
            .header("Accept", "application/json")
            .header("Authorization", "Bearer ${token.trim()}")
        if (method == "GET") builder.get()
        else builder.post((payload ?: JSONObject()).toString().toRequestBody(jsonType))
        return builder.build()
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
        val detail = JSONObject(body).opt("detail")
        when (detail) {
            is JSONObject -> detail.optString("message").ifBlank { detail.toString() }
            is String -> detail
            else -> detail?.toString().orEmpty()
        }
    }.getOrNull().orEmpty().ifBlank { fallback }
}
