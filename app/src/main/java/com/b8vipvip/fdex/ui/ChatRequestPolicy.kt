package com.b8vipvip.fdex.ui

import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.network.parseChatContent

internal fun initialAiStatus(content: String, actorName: String = ""): String {
    val count = parseChatContent(content).attachments.size
    val prefix = actorName.trim().takeIf { it.isNotEmpty() }?.let { "$it " }.orEmpty()
    return if (count == 0) {
        "${prefix}正在连接 AI…"
    } else {
        "${prefix}正在准备 $count 个附件并连接 AI…"
    }
}

internal fun normalizeAiStatus(status: String, hasAttachments: Boolean): String {
    if (hasAttachments || status.isBlank()) return status
    return status
        .replace("FDEX 服务端已接收请求，正在解析附件与选择模型…", "FDEX 服务端已接收文本请求，正在选择模型…")
        .replace("附件解析完成，已选择 ", "已选择 ")
        .replace("附件已上传到 AI 线路", "请求已发送到 AI 线路")
        .replace("服务端正在解析附件正文", "服务端正在准备请求")
}

/**
 * The non-stream request is a compatibility path for a broken/missing SSE tail, not a
 * second attempt for an already-failed provider or an unhealthy FDEX server. Retrying a
 * transport abort/5xx can keep the UI blocked for minutes and doubles pressure on the same
 * upstream provider, so those failures are surfaced immediately with the original request id.
 */
internal fun shouldRetryNonStreamAfterStreamFailure(message: String?): Boolean {
    val value = message.orEmpty().lowercase()
    if (value.isBlank()) return false
    val compatibilitySignals = listOf(
        "流式连接提前结束",
        "服务端没有返回流式响应正文",
        "流式连接已结束，但没有收到正文或媒体结果",
    )
    return compatibilitySignals.any(value::contains)
}

internal fun projectRecordContext(repo: AppRepository, query: String, limit: Int = 20): String {
    if (!shouldIncludeProjectRecords(query)) return ""
    val projects = repo.projects().take(limit.coerceIn(1, 50))
    if (projects.isEmpty()) return "FDEX 本机项目记录：当前账号没有已保存的项目记录。"

    return buildString {
        append("FDEX 本机项目记录（按最近更新时间排序，共 ")
        append(repo.projects().size)
        append(" 个；以下最多展示 ")
        append(projects.size)
        append(" 个）：\n")
        projects.forEachIndexed { index, project ->
            append(index + 1).append(". ")
            append(project.title.ifBlank { "未命名项目" })
            append("｜状态=").append(project.status)
            append("｜需求完整度=").append(project.requirementScore).append('%')
            append("｜创建=").append(project.createdAt)
            append("｜更新=").append(project.updatedAt)
            append("｜备注=").append(repo.notes(project.id).size)
            append("｜资料=").append(repo.assets(project.id).size)
            append("｜报告=").append(repo.reports(project.id).size)
            if (project.description.isNotBlank()) {
                append("\n   描述：").append(project.description.take(500))
            }
            append('\n')
        }
    }.trim()
}

internal fun shouldIncludeProjectRecords(query: String): Boolean {
    val value = query.trim().lowercase()
    if (value.isBlank()) return false
    val direct = listOf("项目", "project", "工作记录", "项目记录", "历史项目", "项目历史")
    if (direct.any(value::contains)) return true
    val history = listOf("之前", "以前", "历史", "过去", "记录", "统计", "汇总").any(value::contains)
    val work = listOf("工作", "任务", "计划", "方案").any(value::contains)
    return history && work
}
