package com.b8vipvip.fdex.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

object LegacyMemoryScopeRegistration {
    private const val KNOWLEDGE_META_PREFS = "fdex_knowledge_meta_v1"
    private const val REMOTE_SCOPE_PREFIX = "remote_memory_scope_"

    fun localScopeTokens(context: Context): List<String> = context.applicationContext
        .getSharedPreferences(KNOWLEDGE_META_PREFS, Context.MODE_PRIVATE)
        .all
        .asSequence()
        .filter { (key, value) -> key.startsWith(REMOTE_SCOPE_PREFIX) && value is String }
        .mapNotNull { (_, value) -> (value as? String)?.trim()?.takeIf { it.length in 24..128 } }
        .distinct()
        .toList()
}

object LocalAccountDataExport {
    fun snapshot(context: Context): JSONObject {
        val app = context.applicationContext
        val userId = CentralSessionStore(app).userId().trim()
        require(userId.isNotBlank()) { "请先登录 FDEX 中心账号" }
        val records = JSONArray()
        FdexLocalDatabase(app).use { database ->
            database.readableDatabase.query(
                "records",
                arrayOf("kind", "id", "parent_id", "sort_key", "payload"),
                null,
                null,
                null,
                null,
                "kind ASC, id ASC",
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    val payload = runCatching { JSONObject(cursor.getString(4)) }
                        .getOrElse { JSONObject().put("raw", cursor.getString(4)) }
                    records.put(
                        JSONObject()
                            .put("kind", cursor.getString(0))
                            .put("id", cursor.getLong(1))
                            .put("parent_id", if (cursor.isNull(2)) JSONObject.NULL else cursor.getLong(2))
                            .put("sort_key", cursor.getString(3))
                            .put("payload", payload),
                    )
                }
            }
        }
        return JSONObject()
            .put("schema_version", 1)
            .put("generated_at", Instant.now().toString())
            .put("user_id", userId)
            .put("records", records)
    }
}
