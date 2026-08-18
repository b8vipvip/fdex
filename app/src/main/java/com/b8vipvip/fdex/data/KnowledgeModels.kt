package com.b8vipvip.fdex.data

import java.security.MessageDigest
import kotlin.math.sqrt

object EmployeeChatAccess {
    const val NONE = "none"
    const val SELF = "self"
    const val ALL = "all"
    const val SELECTED = "selected"

    val values = setOf(NONE, SELF, ALL, SELECTED)

    fun normalize(value: String): String = value.takeIf { it in values } ?: SELF
}

data class EmployeePermissions(
    val knowledgeRead: Boolean = false,
    val knowledgeWrite: Boolean = false,
    val chatAccessMode: String = EmployeeChatAccess.SELF,
    val readableEmployeeIds: List<Long> = emptyList(),
)

data class KnowledgeEntry(
    val id: Long,
    val scopeKey: String,
    val wing: String,
    val room: String,
    val title: String,
    val summary: String,
    val keywords: List<String>,
    val rawText: String,
    val conversationId: String,
    val source: String,
    val sourceEmployeeId: Long?,
    val sourceEmployeeName: String,
    val sourceMessageIds: List<Long>,
    val contentHash: String,
    val sharedForAgents: Boolean,
    val needsEnrichment: Boolean,
    val createdAt: String,
    val updatedAt: String,
    val archived: Boolean = false,
)

data class KnowledgeDraft(
    val room: String,
    val title: String,
    val summary: String,
    val keywords: List<String>,
)

data class KnowledgeSearchHit(
    val entry: KnowledgeEntry,
    val score: Double,
)

object KnowledgeRooms {
    const val GENERAL = "general"
    const val BUSINESS = "business"
    const val DECISION = "decision"
    const val PROJECT = "project"
    const val CUSTOMER = "customer"
    const val PRODUCT = "product"
    const val OPERATIONS = "operations"
    const val TECHNICAL = "technical"
    const val FINANCE = "finance"
    const val HR = "hr"
    const val PERSONAL = "personal"
    const val CASUAL = "casual"

    val ordered = listOf(
        BUSINESS,
        DECISION,
        PROJECT,
        CUSTOMER,
        PRODUCT,
        OPERATIONS,
        TECHNICAL,
        FINANCE,
        HR,
        PERSONAL,
        CASUAL,
        GENERAL,
    )

    fun normalize(value: String): String = value.trim().lowercase().takeIf { it in ordered } ?: GENERAL

    fun label(value: String): String = when (normalize(value)) {
        BUSINESS -> "业务知识"
        DECISION -> "决策结论"
        PROJECT -> "项目任务"
        CUSTOMER -> "客户销售"
        PRODUCT -> "产品需求"
        OPERATIONS -> "运营流程"
        TECHNICAL -> "技术开发"
        FINANCE -> "财务经营"
        HR -> "人事组织"
        PERSONAL -> "个人偏好"
        CASUAL -> "日常闲聊"
        else -> "其他知识"
    }
}

object KnowledgeEngine {
    private const val HASH_DIMENSIONS = 192
    private val whitespace = Regex("\\s+")
    private val latinWords = Regex("[A-Za-z0-9_+.#/-]{2,}")
    private val chineseRuns = Regex("[\\u4e00-\\u9fff]{2,}")
    private val businessSignals = listOf(
        "项目", "订单", "价格", "客户", "代码", "接口", "服务器", "需求", "方案", "问题", "故障",
        "产品", "运营", "数据", "财务", "合同", "员工", "权限", "知识库", "怎么", "如何", "为什么",
    )
    private val casualSignals = listOf(
        "你好", "您好", "嗨", "哈喽", "在吗", "在么", "哈哈", "嘿嘿", "谢谢", "好的", "好呀", "嗯嗯",
        "收到", "晚安", "早安", "早上好", "下午好", "晚上好", "拜拜", "再见", "哦哦",
    )
    private val stopWords = setOf(
        "用户", "员工", "回复", "这个", "那个", "可以", "需要", "已经", "还是", "然后", "就是", "一下",
        "什么", "怎么", "如何", "我们", "你们", "他们", "自己", "当前", "进行", "一个", "没有", "如果",
    )

    fun fallbackDraft(rawText: String, sourceEmployeeName: String = ""): KnowledgeDraft {
        val compact = compact(rawText)
        val room = if (isCasual(compact)) KnowledgeRooms.CASUAL else detectRoom(compact)
        val title = when {
            room == KnowledgeRooms.CASUAL && sourceEmployeeName.isNotBlank() -> "与${sourceEmployeeName}的日常闲聊"
            room == KnowledgeRooms.CASUAL -> "日常问候与闲聊"
            else -> firstUserText(rawText).ifBlank { compact }.take(48).ifBlank { KnowledgeRooms.label(room) }
        }
        return KnowledgeDraft(
            room = room,
            title = title,
            summary = compact.take(320).ifBlank { "暂无可整理正文" },
            keywords = extractKeywords(compact),
        )
    }

    fun contentHash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    fun search(entries: List<KnowledgeEntry>, query: String, limit: Int = 30): List<KnowledgeSearchHit> {
        val active = entries.filterNot { it.archived }
        val trimmed = query.trim()
        if (trimmed.isBlank()) {
            return active
                .sortedByDescending { it.updatedAt }
                .take(limit)
                .map { KnowledgeSearchHit(it, 1.0) }
        }
        return active.map { entry ->
            val indexText = buildString {
                append(entry.title).append('\n')
                append(entry.summary).append('\n')
                append(entry.keywords.joinToString(" ")).append('\n')
                append(KnowledgeRooms.label(entry.room)).append('\n')
                append(entry.sourceEmployeeName).append('\n')
                append(entry.rawText.take(5000))
            }
            KnowledgeSearchHit(entry, scoreText(trimmed, indexText, entry.keywords))
        }
            .filter { it.score > 0.02 }
            .sortedWith(compareByDescending<KnowledgeSearchHit> { it.score }.thenByDescending { it.entry.updatedAt })
            .take(limit)
    }

    fun scoreText(query: String, text: String, keywords: List<String> = emptyList()): Double {
        val q = compact(query).lowercase()
        val body = compact(text).lowercase()
        if (q.isBlank() || body.isBlank()) return 0.0
        val qTokens = tokenize(q)
        val bTokens = tokenize(body)
        if (qTokens.isEmpty() || bTokens.isEmpty()) return if (body.contains(q)) 1.0 else 0.0

        val overlap = qTokens.count { it in bTokens }.toDouble() / qTokens.size.coerceAtLeast(1)
        val phraseBoost = if (body.contains(q)) 1.8 else 0.0
        val keywordBoost = keywords.count { keyword ->
            val normalized = keyword.lowercase().trim()
            normalized.isNotBlank() && (q.contains(normalized) || normalized.contains(q) || qTokens.any { normalized.contains(it) })
        } * 0.35
        val hashed = cosine(hashedVector(qTokens), hashedVector(bTokens))
        return overlap * 2.4 + hashed * 1.2 + phraseBoost + keywordBoost
    }

    fun extractKeywords(text: String, max: Int = 8): List<String> {
        val compact = compact(text)
        if (compact.isBlank()) return emptyList()
        val candidates = mutableListOf<String>()
        latinWords.findAll(compact).forEach { candidates += it.value.lowercase() }
        chineseRuns.findAll(compact).forEach { match ->
            val run = match.value
            if (run.length <= 8) {
                candidates += run
            } else {
                var start = 0
                while (start < run.length) {
                    candidates += run.substring(start, minOf(start + 6, run.length))
                    start += 4
                }
            }
        }
        return candidates
            .map { it.trim('，', '。', '！', '？', '：', '；', ',', '.', ':', ';') }
            .filter { it.length >= 2 && it !in stopWords && !it.all(Char::isDigit) }
            .groupingBy { it }
            .eachCount()
            .entries
            .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenByDescending { it.key.length })
            .map { it.key }
            .distinct()
            .take(max)
    }

    private fun detectRoom(text: String): String {
        val lower = text.lowercase()
        return when {
            listOf("代码", "接口", "api", "github", "服务器", "android", "kotlin", "python", "报错", "日志").any(lower::contains) -> KnowledgeRooms.TECHNICAL
            listOf("客户", "买家", "销售", "成交", "售后", "客服").any(lower::contains) -> KnowledgeRooms.CUSTOMER
            listOf("产品", "功能", "需求", "版本", "体验").any(lower::contains) -> KnowledgeRooms.PRODUCT
            listOf("运营", "投放", "活动", "流量", "转化", "流程").any(lower::contains) -> KnowledgeRooms.OPERATIONS
            listOf("财务", "成本", "利润", "预算", "收入", "税").any(lower::contains) -> KnowledgeRooms.FINANCE
            listOf("招聘", "人事", "绩效", "岗位", "员工管理").any(lower::contains) -> KnowledgeRooms.HR
            listOf("决定", "结论", "确定", "采用", "不再", "最终").any(lower::contains) -> KnowledgeRooms.DECISION
            listOf("项目", "任务", "计划", "里程碑", "进度").any(lower::contains) -> KnowledgeRooms.PROJECT
            listOf("喜欢", "偏好", "习惯", "以后", "记住").any(lower::contains) -> KnowledgeRooms.PERSONAL
            businessSignals.any(lower::contains) -> KnowledgeRooms.BUSINESS
            else -> KnowledgeRooms.GENERAL
        }
    }

    private fun isCasual(text: String): Boolean {
        if (text.isBlank() || text.length > 80) return false
        val lower = text.lowercase()
        if (businessSignals.any(lower::contains)) return false
        return casualSignals.any(lower::contains)
    }

    private fun firstUserText(rawText: String): String {
        val line = rawText.lineSequence().firstOrNull { it.trim().startsWith("用户：") } ?: return ""
        return line.substringAfter("用户：").trim()
    }

    private fun compact(value: String): String = value.replace(whitespace, " ").trim()

    private fun tokenize(value: String): Set<String> {
        val result = linkedSetOf<String>()
        latinWords.findAll(value).forEach { result += it.value.lowercase() }
        chineseRuns.findAll(value).forEach { match ->
            val run = match.value
            if (run.length == 2) result += run
            if (run.length >= 3) {
                for (index in 0 until run.length - 1) result += run.substring(index, index + 2)
            }
        }
        return result
    }

    private fun hashedVector(tokens: Set<String>): DoubleArray {
        val vector = DoubleArray(HASH_DIMENSIONS)
        tokens.forEach { token ->
            val digest = MessageDigest.getInstance("SHA-256").digest(token.toByteArray(Charsets.UTF_8))
            val raw = ((digest[0].toInt() and 0xff) shl 8) or (digest[1].toInt() and 0xff)
            val bucket = raw % HASH_DIMENSIONS
            val sign = if ((digest[2].toInt() and 1) == 0) 1.0 else -1.0
            vector[bucket] += sign
        }
        return vector
    }

    private fun cosine(a: DoubleArray, b: DoubleArray): Double {
        var dot = 0.0
        var aa = 0.0
        var bb = 0.0
        for (index in a.indices) {
            dot += a[index] * b[index]
            aa += a[index] * a[index]
            bb += b[index] * b[index]
        }
        if (aa == 0.0 || bb == 0.0) return 0.0
        return (dot / (sqrt(aa) * sqrt(bb))).coerceAtLeast(0.0)
    }
}
