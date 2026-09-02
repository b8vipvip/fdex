package com.b8vipvip.fdex.diagnostics

import android.content.Context
import android.os.Build
import android.util.Log
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.network.ClientDiagnosticsApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.time.Instant

/**
 * Bounded, app-private client diagnostic queue.
 *
 * It deliberately does not collect chat text, attachment contents, passwords, auth headers or
 * tokens. Callers should log operational metadata only. A second redaction pass also runs here and
 * again on the server before any record becomes visible in the admin console.
 */
object ClientRuntimeLog {
    private const val TAG = "FDEXClient"
    private const val QUEUE_FILE = "fdex-client-runtime-logs.jsonl"
    private const val MAX_QUEUE_BYTES = 1024 * 1024L
    private const val TRIM_TO_LINES = 500
    private const val BATCH_SIZE = 50
    private val lock = Any()
    @Volatile private var appContext: Context? = null
    @Volatile private var installed = false

    fun install(context: Context) {
        appContext = context.applicationContext
        if (!installed) {
            synchronized(lock) {
                if (!installed) {
                    val previous = Thread.getDefaultUncaughtExceptionHandler()
                    Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
                        runCatching {
                            error(
                                component = "process",
                                event = "uncaught_exception",
                                message = "${throwable::class.java.simpleName}: ${throwable.message.orEmpty()}",
                                details = mapOf(
                                    "thread" to thread.name,
                                    "stack" to throwable.stackTraceToString().take(6000),
                                ),
                            )
                        }
                        previous?.uncaughtException(thread, throwable)
                    }
                    installed = true
                }
            }
        }
        info(
            component = "app",
            event = "app_start",
            message = "FDEX Android client started",
            details = mapOf(
                "version" to BuildConfig.VERSION_NAME,
                "git_sha" to BuildConfig.GIT_SHA,
                "sdk" to Build.VERSION.SDK_INT,
                "device" to "${Build.MANUFACTURER} ${Build.MODEL}".trim(),
            ),
        )
    }

    fun debug(component: String, event: String, message: String = "", details: Map<String, Any?> = emptyMap()) =
        append("debug", component, event, message, details)

    fun info(component: String, event: String, message: String = "", details: Map<String, Any?> = emptyMap()) =
        append("info", component, event, message, details)

    fun warn(component: String, event: String, message: String = "", details: Map<String, Any?> = emptyMap()) =
        append("warn", component, event, message, details)

    fun error(component: String, event: String, message: String = "", details: Map<String, Any?> = emptyMap()) =
        append("error", component, event, message, details)

    private fun append(level: String, component: String, event: String, message: String, details: Map<String, Any?>) {
        val context = appContext ?: return
        val safeDetails = JSONObject()
        details.entries.take(40).forEach { (key, value) ->
            val safeKey = sanitizeText(key, 80)
            if (looksSensitiveKey(safeKey)) safeDetails.put(safeKey, "[REDACTED]")
            else safeDetails.put(safeKey, sanitizeValue(value))
        }
        val record = JSONObject()
            .put("time", Instant.now().toString())
            .put("level", level)
            .put("component", sanitizeText(component, 120).ifBlank { "client" })
            .put("event", sanitizeText(event, 160).ifBlank { "event" })
            .put("message", sanitizeText(message, 2000))
            .put("details", safeDetails)
        val line = record.toString()
        synchronized(lock) {
            runCatching {
                val file = queueFile(context)
                file.parentFile?.mkdirs()
                file.appendText(line + "\n", Charsets.UTF_8)
                if (file.length() > MAX_QUEUE_BYTES) trimQueue(file)
            }
        }
        when (level) {
            "error" -> Log.e(TAG, "${record.optString("component")}/${record.optString("event")}: ${record.optString("message")}")
            "warn" -> Log.w(TAG, "${record.optString("component")}/${record.optString("event")}: ${record.optString("message")}")
            else -> Log.i(TAG, "${record.optString("component")}/${record.optString("event")}: ${record.optString("message")}")
        }
    }

    suspend fun flush(context: Context): Int = withContext(Dispatchers.IO) {
        appContext = context.applicationContext
        val pending = synchronized(lock) { readPending(queueFile(context), BATCH_SIZE) }
        if (pending.isEmpty()) return@withContext 0
        if (!ClientDiagnosticsApi.upload(context.applicationContext, pending)) return@withContext 0
        synchronized(lock) { removeFirst(queueFile(context), pending.size) }
        pending.size
    }

    internal fun sanitizeText(raw: String?, limit: Int = 2000): String {
        var text = raw.orEmpty().replace('\u0000', ' ').trim()
        val patterns = listOf(
            Regex("(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s,;]+"),
            Regex("(?i)(bearer\\s+)[A-Za-z0-9._~+/=-]{16,}"),
            Regex("(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|cookie)\\s*[:=]\\s*)[^\\s,;]+"),
        )
        patterns.forEach { pattern ->
            text = pattern.replace(text) { match -> match.groupValues[1] + "[REDACTED]" }
        }
        return text.take(limit.coerceAtLeast(0))
    }

    private fun looksSensitiveKey(key: String): Boolean {
        val lowered = key.lowercase()
        return listOf("password", "passwd", "token", "secret", "authorization", "cookie", "api_key", "apikey")
            .any(lowered::contains)
    }

    private fun sanitizeValue(value: Any?): Any = when (value) {
        null -> JSONObject.NULL
        is Boolean, is Int, is Long, is Float, is Double -> value
        else -> sanitizeText(value.toString(), 1200)
    }

    private fun queueFile(context: Context): File = File(context.filesDir, QUEUE_FILE)

    private fun readPending(file: File, limit: Int): List<JSONObject> {
        if (!file.isFile) return emptyList()
        return file.useLines { lines ->
            lines.mapNotNull { line -> runCatching { JSONObject(line) }.getOrNull() }
                .take(limit)
                .toList()
        }
    }

    private fun removeFirst(file: File, count: Int) {
        if (!file.isFile || count <= 0) return
        val remaining = file.readLines(Charsets.UTF_8).drop(count)
        if (remaining.isEmpty()) file.delete()
        else file.writeText(remaining.joinToString("\n", postfix = "\n"), Charsets.UTF_8)
    }

    private fun trimQueue(file: File) {
        val tail = file.readLines(Charsets.UTF_8).takeLast(TRIM_TO_LINES)
        file.writeText(tail.joinToString("\n", postfix = if (tail.isEmpty()) "" else "\n"), Charsets.UTF_8)
    }
}
