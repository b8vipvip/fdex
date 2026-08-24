package com.b8vipvip.fdex.data

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import java.security.MessageDigest
import java.time.Instant


data class LegacyMigrationStatus(
    val legacyExists: Boolean,
    val legacyRecords: Int,
    val currentRecords: Int,
    val eligible: Boolean,
    val message: String,
)

object LegacyDataMigration {
    private const val LEGACY_DATABASE_NAME = "fdex-local-v3.db"
    private const val DEFAULT_NEXT_ID = 1000L

    fun status(context: Context): LegacyMigrationStatus {
        val app = context.applicationContext
        val userId = CentralSessionStore(app).userId().trim()
        if (userId.isBlank()) return LegacyMigrationStatus(false, 0, 0, false, "请先登录 FDEX 中心账号")
        val legacy = app.getDatabasePath(LEGACY_DATABASE_NAME)
        if (!legacy.exists()) return LegacyMigrationStatus(false, 0, countCurrent(app, userId), false, "没有发现旧版本机数据库")
        val legacyRecords = countRecords(legacy.absolutePath)
        val currentRecords = countCurrent(app, userId)
        val eligible = legacyRecords > 0 && currentRecords == 0
        val message = when {
            legacyRecords <= 0 -> "旧版本机数据库为空，无需迁移"
            currentRecords > 0 -> "当前 FDEX 账号已经有本机数据，为避免覆盖或串号，本版本不会自动合并两套非空数据库"
            else -> "可把旧版本机数据复制到当前 FDEX 账号；旧数据库会保留作为备份"
        }
        return LegacyMigrationStatus(true, legacyRecords, currentRecords, eligible, message)
    }

    fun migrateToCurrentAccount(context: Context): Result<Int> = runCatching {
        val app = context.applicationContext
        val userId = CentralSessionStore(app).userId().trim()
        require(userId.isNotBlank()) { "请先登录 FDEX 中心账号" }
        val state = status(app)
        require(state.eligible) { state.message }
        val legacy = app.getDatabasePath(LEGACY_DATABASE_NAME).absoluteFile
        val targetName = scopedDatabaseName(userId)
        FdexLocalDatabase(app).use { it.writableDatabase }
        val target = app.getDatabasePath(targetName).absoluteFile
        require(target.exists()) { "当前账号数据库尚未创建" }

        val db = SQLiteDatabase.openDatabase(target.absolutePath, null, SQLiteDatabase.OPEN_READWRITE)
        try {
            db.execSQL("ATTACH DATABASE ? AS legacy_db", arrayOf(legacy.absolutePath))
            db.beginTransaction()
            try {
                val current = db.rawQuery("SELECT COUNT(*) FROM records", null).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }
                require(current == 0) { "当前账号已经产生本机数据，已取消迁移" }
                db.execSQL(
                    "INSERT INTO records(kind,id,parent_id,sort_key,payload) SELECT kind,id,parent_id,sort_key,payload FROM legacy_db.records",
                )
                val maxId = db.rawQuery("SELECT COALESCE(MAX(id),0) FROM records", null).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else 0L }
                val nextId = maxOf(DEFAULT_NEXT_ID, maxId + 1L)
                db.execSQL("INSERT OR REPLACE INTO meta(key,value) VALUES('next_id',?)", arrayOf(nextId.toString()))
                db.execSQL("INSERT OR REPLACE INTO meta(key,value) VALUES('legacy_account_imported_at',?)", arrayOf(Instant.now().toString()))
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
            db.execSQL("DETACH DATABASE legacy_db")
        } finally {
            db.close()
        }
        state.legacyRecords
    }

    fun deleteCurrentAccountDatabase(context: Context): Boolean {
        val app = context.applicationContext
        val userId = CentralSessionStore(app).userId().trim()
        if (userId.isBlank()) return false
        return app.deleteDatabase(scopedDatabaseName(userId))
    }

    private fun countCurrent(context: Context, userId: String): Int {
        val file = context.getDatabasePath(scopedDatabaseName(userId))
        return if (file.exists()) countRecords(file.absolutePath) else 0
    }

    private fun countRecords(path: String): Int = runCatching {
        SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READONLY).use { db ->
            db.rawQuery("SELECT COUNT(*) FROM records", null).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }
        }
    }.getOrDefault(0)

    internal fun scopedDatabaseName(userId: String): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(userId.trim().toByteArray())
            .joinToString("") { "%02x".format(it) }.take(24)
        return "fdex-local-user-$digest.db"
    }
}
