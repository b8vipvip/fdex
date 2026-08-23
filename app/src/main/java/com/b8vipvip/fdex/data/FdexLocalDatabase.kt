package com.b8vipvip.fdex.data

import android.content.ContentValues
import android.content.Context
import android.content.SharedPreferences
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject

/**
 * Row-oriented local store for FDEX business data.
 *
 * Older releases stored every entity type as one large JSON array inside
 * SharedPreferences. That made every append/update rewrite the whole array and
 * became increasingly expensive as chats and works grew. This database keeps
 * one SQLite row per entity while preserving the existing JSON wire shape, so
 * AppRepository can migrate existing users without a destructive schema reset.
 */
internal class FdexLocalDatabase(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DATABASE_NAME, null, DATABASE_VERSION) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE records (
                kind TEXT NOT NULL,
                id INTEGER NOT NULL,
                parent_id INTEGER,
                sort_key TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL,
                PRIMARY KEY(kind, id)
            )
            """.trimIndent(),
        )
        db.execSQL("CREATE INDEX idx_records_kind_parent_sort ON records(kind, parent_id, sort_key, id)")
        db.execSQL("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    @Synchronized
    fun nextId(): Long {
        val db = writableDatabase
        db.beginTransaction()
        return try {
            val current = meta(db, META_NEXT_ID)?.toLongOrNull() ?: DEFAULT_NEXT_ID
            val next = current + 1L
            putMeta(db, META_NEXT_ID, next.toString())
            db.setTransactionSuccessful()
            next
        } finally {
            db.endTransaction()
        }
    }

    fun upsert(kind: String, id: Long, parentId: Long?, sortKey: String, payload: JSONObject) {
        val values = ContentValues().apply {
            put("kind", kind)
            put("id", id)
            if (parentId == null) putNull("parent_id") else put("parent_id", parentId)
            put("sort_key", sortKey)
            put("payload", payload.toString())
        }
        writableDatabase.insertWithOnConflict("records", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun query(kind: String, parentId: Long? = null): List<JSONObject> {
        val selection: String
        val args: Array<String>
        if (parentId == null) {
            selection = "kind=?"
            args = arrayOf(kind)
        } else {
            selection = "kind=? AND parent_id=?"
            args = arrayOf(kind, parentId.toString())
        }
        return readableDatabase.query(
            "records",
            arrayOf("payload"),
            selection,
            args,
            null,
            null,
            "sort_key ASC, id ASC",
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    runCatching { JSONObject(cursor.getString(0)) }.getOrNull()?.let(::add)
                }
            }
        }
    }

    fun queryById(kind: String, id: Long): JSONObject? = readableDatabase.query(
        "records",
        arrayOf("payload"),
        "kind=? AND id=?",
        arrayOf(kind, id.toString()),
        null,
        null,
        null,
        "1",
    ).use { cursor ->
        if (!cursor.moveToFirst()) return@use null
        runCatching { JSONObject(cursor.getString(0)) }.getOrNull()
    }

    fun delete(kind: String, id: Long) {
        writableDatabase.delete(
            "records",
            "kind=? AND id=?",
            arrayOf(kind, id.toString()),
        )
    }

    fun clearAll() {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.delete("records", null, null)
            putMeta(db, META_NEXT_ID, DEFAULT_NEXT_ID.toString())
            putMeta(db, META_LEGACY_MIGRATED, "1")
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    /** Import legacy fdex_app_v2 JSON arrays exactly once. */
    fun migrateLegacyIfNeeded(prefs: SharedPreferences) {
        val db = writableDatabase
        if (meta(db, META_LEGACY_MIGRATED) == "1") return

        db.beginTransaction()
        try {
            var maxId = prefs.getLong("next_id", DEFAULT_NEXT_ID).coerceAtLeast(DEFAULT_NEXT_ID)
            LEGACY_KIND_KEYS.forEach { (kind, prefKey) ->
                val raw = prefs.getString(prefKey, "[]").orEmpty()
                val array = runCatching { JSONArray(raw) }.getOrDefault(JSONArray())
                for (index in 0 until array.length()) {
                    val item = array.optJSONObject(index) ?: continue
                    val id = item.optLong("id", -1L)
                    if (id <= 0L) continue
                    maxId = maxOf(maxId, id)
                    insertLegacy(db, kind, item)
                }
            }
            putMeta(db, META_NEXT_ID, maxId.toString())
            putMeta(db, META_LEGACY_MIGRATED, "1")
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }

        // Only remove business arrays after the SQLite transaction is durable.
        // Account/session/profile preferences intentionally remain in the small
        // private preference file because they are not growing business data.
        prefs.edit().apply {
            LEGACY_KIND_KEYS.forEach { (_, key) -> remove(key) }
            remove("next_id")
            putBoolean("business_sqlite_v3", true)
        }.apply()
    }

    private fun insertLegacy(db: SQLiteDatabase, kind: String, item: JSONObject) {
        val id = item.optLong("id")
        val parentId = when (kind) {
            KIND_MESSAGE -> item.optLong("employee").takeIf { it > 0 }
            KIND_NOTE, KIND_ASSET, KIND_REPORT -> item.optLong("project").takeIf { it > 0 }
            KIND_GROUP_MESSAGE -> item.optLong("group").takeIf { it > 0 }
            KIND_GROUP -> if (item.isNull("project")) null else item.optLong("project").takeIf { it > 0 }
            else -> null
        }
        val sortKey = when (kind) {
            KIND_PROJECT, KIND_GROUP -> item.optString("updated").ifBlank { id.toString().padStart(20, '0') }
            else -> id.toString().padStart(20, '0')
        }
        val values = ContentValues().apply {
            put("kind", kind)
            put("id", id)
            if (parentId == null) putNull("parent_id") else put("parent_id", parentId)
            put("sort_key", sortKey)
            put("payload", item.toString())
        }
        db.insertWithOnConflict("records", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    private fun meta(db: SQLiteDatabase, key: String): String? = db.query(
        "meta",
        arrayOf("value"),
        "key=?",
        arrayOf(key),
        null,
        null,
        null,
        "1",
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getString(0) else null }

    private fun putMeta(db: SQLiteDatabase, key: String, value: String) {
        val values = ContentValues().apply {
            put("key", key)
            put("value", value)
        }
        db.insertWithOnConflict("meta", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    companion object {
        const val KIND_EMPLOYEE = "employee"
        const val KIND_MESSAGE = "message"
        const val KIND_PROJECT = "project"
        const val KIND_NOTE = "note"
        const val KIND_ASSET = "asset"
        const val KIND_REPORT = "report"
        const val KIND_GROUP = "group"
        const val KIND_GROUP_MESSAGE = "group_message"
        const val KIND_KNOWLEDGE = "knowledge"
        const val KIND_EMPLOYEE_PERMISSION = "employee_permission"

        private const val DATABASE_NAME = "fdex-local-v3.db"
        private const val DATABASE_VERSION = 1
        private const val DEFAULT_NEXT_ID = 1000L
        private const val META_NEXT_ID = "next_id"
        private const val META_LEGACY_MIGRATED = "legacy_shared_preferences_migrated"

        private val LEGACY_KIND_KEYS = listOf(
            KIND_EMPLOYEE to "employees",
            KIND_MESSAGE to "messages",
            KIND_PROJECT to "projects",
            KIND_NOTE to "notes",
            KIND_ASSET to "assets",
            KIND_REPORT to "reports",
            KIND_GROUP to "groups",
            KIND_GROUP_MESSAGE to "group_messages",
        )
    }
}
