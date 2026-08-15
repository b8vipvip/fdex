package com.b8vipvip.fdex.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.GroupMessage
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.AiMediaResult
import com.b8vipvip.fdex.network.AiStreamEvent
import com.b8vipvip.fdex.network.ClientAiApi
import com.b8vipvip.fdex.network.RealtimeVoiceSession
import com.b8vipvip.fdex.network.encodeAiMediaMarker
import com.b8vipvip.fdex.network.parseChatContent
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

private const val STREAM_UI_THROTTLE_NS = 50_000_000L

private data class CollectedReply(
    val content: String,
    val failure: String?,
)

@Composable
internal fun StreamingEmployeeChatScreen(
    repo: AppRepository,
    employeeId: Long,
    revision: Int,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val employee = repo.employee(employeeId) ?: return
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var realtimeVoiceActive by remember { mutableStateOf(false) }
    var realtimeSession by remember { mutableStateOf<RealtimeVoiceSession?>(null) }
    var streamMarkdown by remember { mutableStateOf("") }
    var streamStatus by remember { mutableStateOf("") }
    var streamReasoning by remember { mutableStateOf("") }

    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f)) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(
                    start = 16.dp,
                    end = 16.dp,
                    top = if (realtimeVoiceActive) 84.dp else 12.dp,
                    bottom = 12.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(18.dp),
            ) {
                items(repo.messages(employeeId), key = { it.id }) { message ->
                    EmployeeChatMessage(message)
                }
                if (busy) {
                    item(key = "streaming-$employeeId") {
                        LiveAssistantMessage(
                            markdown = streamMarkdown,
                            status = streamStatus,
                            reasoning = streamReasoning,
                        )
                    }
                }
            }

            if (realtimeVoiceActive) {
                RealtimeVoiceBar(
                    employeeName = employee.name,
                    system = employeeSystemPrompt(employee),
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    onEnd = {
                        realtimeSession = null
                        realtimeVoiceActive = false
                    },
                    onSessionChanged = { realtimeSession = it },
                    onUserTranscript = { transcript ->
                        if (transcript.isNotBlank()) {
                            repo.addMessage(employeeId, "user", transcript)
                            onChanged()
                        }
                    },
                    onAssistantReply = { reply ->
                        if (reply.isNotBlank()) {
                            repo.addMessage(employeeId, "employee", reply)
                            onChanged()
                        }
                    },
                )
            }
        }

        AttachmentChatComposer(
            value = text,
            placeholder = "给员工安排任务…",
            busy = busy,
            onValueChange = { text = it },
            onRealtimeVoice = { realtimeVoiceActive = true },
            realtimeVoiceActive = realtimeVoiceActive,
            onSend = { messageContent ->
                val parsed = parseChatContent(messageContent)
                if (realtimeVoiceActive && parsed.attachments.isEmpty()) {
                    val realtimeText = parsed.text.trim()
                    if (realtimeText.isBlank()) return@AttachmentChatComposer
                    val sent = realtimeSession?.sendText(realtimeText) == true
                    if (sent) {
                        text = ""
                        repo.addMessage(employeeId, "user", realtimeText)
                        onChanged()
                    } else {
                        scope.launch {
                            snackbar.showSnackbar("实时语音正在连接，文字未发送；不会切换供应商或模型，请稍后重试")
                        }
                    }
                    return@AttachmentChatComposer
                }

                text = ""
                repo.addMessage(employeeId, "user", messageContent)
                onChanged()
                busy = true
                streamMarkdown = ""
                streamStatus = "正在准备附件并连接 AI…"
                streamReasoning = ""
                scope.launch {
                    val result = collectStreamedReply(
                        context = context,
                        system = employeeSystemPrompt(employee),
                        prompt = messageContent,
                        onStatus = { streamStatus = it },
                        onMarkdown = { streamMarkdown = it },
                        onReasoning = { streamReasoning = it },
                    )
                    if (result.content.isNotBlank()) {
                        repo.addMessage(employeeId, "employee", result.content)
                    } else {
                        val message = result.failure ?: "AI 没有返回正文或媒体内容"
                        repo.addMessage(employeeId, "employee", "暂时无法完成请求：$message")
                        snackbar.showSnackbar(message)
                    }
                    busy = false
                    streamMarkdown = ""
                    streamStatus = ""
                    streamReasoning = ""
                    onChanged()
                }
            },
        )
    }
}

@Composable
internal fun StreamingGroupChatScreen(
    repo: AppRepository,
    groupId: Long,
    revision: Int,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val group = repo.group(groupId) ?: return
    val members = group.memberIds.mapNotNull { repo.employee(it) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var liveEmployee by remember { mutableStateOf<Employee?>(null) }
    var streamMarkdown by remember { mutableStateOf("") }
    var streamStatus by remember { mutableStateOf("") }
    var streamReasoning by remember { mutableStateOf("") }

    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        Card(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 8.dp)) {
            Column(Modifier.padding(12.dp)) {
                Text(group.name, fontWeight = FontWeight.Bold)
                Text("${members.size} 名成员 · ${if (group.autoMode) "自动运营" else "人工指挥"}", color = Emerald)
                if (group.description.isNotBlank()) Text(group.description, color = Muted)
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            items(repo.groupMessages(groupId), key = { it.id }) { message ->
                GroupChatMessage(message)
            }
            if (busy) {
                item(key = "streaming-group-$groupId") {
                    Column(Modifier.fillMaxWidth()) {
                        liveEmployee?.let {
                            Text(it.name, color = Muted, style = MaterialTheme.typography.labelMedium)
                        }
                        LiveAssistantMessage(
                            markdown = streamMarkdown,
                            status = streamStatus,
                            reasoning = streamReasoning,
                        )
                    }
                }
            }
        }

        AttachmentChatComposer(
            value = text,
            placeholder = "@员工 或安排团队任务…",
            busy = busy,
            onValueChange = { text = it },
            onSend = { messageContent ->
                val visiblePrompt = parseChatContent(messageContent).text
                text = ""
                repo.addGroupMessage(groupId, "user", "我", messageContent)
                onChanged()
                val target = members.firstOrNull {
                    visiblePrompt.contains("@${it.name}") || visiblePrompt.contains(it.position)
                } ?: members.firstOrNull()
                if (target == null) {
                    repo.addGroupMessage(groupId, "system", "", "当前群里还没有 AI 员工。")
                    onChanged()
                    return@AttachmentChatComposer
                }

                busy = true
                liveEmployee = target
                streamMarkdown = ""
                streamStatus = "${target.name} 正在准备附件并连接 AI…"
                streamReasoning = ""
                scope.launch {
                    val result = collectStreamedReply(
                        context = context,
                        system = groupSystemPrompt(target),
                        prompt = messageContent,
                        onStatus = { streamStatus = it },
                        onMarkdown = { streamMarkdown = it },
                        onReasoning = { streamReasoning = it },
                    )
                    val reply = if (result.content.isNotBlank()) {
                        result.content
                    } else {
                        "暂时无法完成：${result.failure ?: "AI 没有返回正文或媒体内容"}"
                    }
                    repo.addGroupMessage(groupId, "employee", target.name, reply)
                    if (result.content.isBlank()) snackbar.showSnackbar(result.failure ?: "AI 没有返回正文或媒体内容")
                    busy = false
                    liveEmployee = null
                    streamMarkdown = ""
                    streamStatus = ""
                    streamReasoning = ""
                    onChanged()
                }
            },
        )
    }
}

@Composable
private fun EmployeeChatMessage(message: ChatMessage) {
    if (message.role == "user") {
        AttachmentUserMessage(message.content)
    } else {
        AssistantMessage(message.content)
    }
}

@Composable
private fun GroupChatMessage(message: GroupMessage) {
    when (message.role) {
        "user" -> AttachmentUserMessage(message.content)
        "system" -> {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                Surface(
                    shape = RoundedCornerShape(14.dp),
                    color = Color(0xFFF1F5F9),
                ) {
                    Text(
                        message.content,
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp),
                    )
                }
            }
        }
        else -> {
            Column(Modifier.fillMaxWidth()) {
                if (message.employeeName.isNotBlank()) {
                    Text(
                        message.employeeName,
                        color = Muted,
                        style = MaterialTheme.typography.labelMedium,
                        modifier = Modifier.padding(bottom = 5.dp),
                    )
                }
                AssistantMessage(message.content)
            }
        }
    }
}

@Composable
private fun AssistantMessage(markdown: String) {
    RichAssistantMessage(markdown, modifier = Modifier.fillMaxWidth())
}

@Composable
private fun LiveAssistantMessage(markdown: String, status: String, reasoning: String) {
    Column(Modifier.fillMaxWidth()) {
        if (status.isNotBlank()) {
            Text(
                status,
                color = Muted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
        if (reasoning.isNotBlank()) {
            Surface(
                modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                shape = RoundedCornerShape(10.dp),
                color = Color(0xFFF5F5F5),
            ) {
                Column(Modifier.padding(horizontal = 12.dp, vertical = 9.dp)) {
                    Text("思考摘要", color = Muted, style = MaterialTheme.typography.labelSmall)
                    Text(
                        reasoning.takeLast(1200),
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 3.dp),
                    )
                }
            }
        }
        if (markdown.isNotBlank()) {
            RichAssistantMessage(markdown, modifier = Modifier.fillMaxWidth())
        } else if (status.isBlank()) {
            Text("正在等待正文或媒体结果…", color = Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

private fun appendMedia(raw: StringBuilder, media: AiMediaResult) {
    val marker = encodeAiMediaMarker(media)
    if (!raw.contains(marker)) {
        if (raw.isNotEmpty() && !raw.endsWith("\n")) raw.append("\n\n")
        raw.append(marker)
    }
}

private suspend fun collectStreamedReply(
    context: Context,
    system: String?,
    prompt: String,
    onStatus: (String) -> Unit,
    onMarkdown: (String) -> Unit,
    onReasoning: (String) -> Unit,
): CollectedReply {
    val raw = StringBuilder()
    val reasoning = StringBuilder()
    var lastContentFlush = 0L
    var lastReasoningFlush = 0L
    var failure: String? = null

    ClientAiApi.streamAsk(system, prompt, context = context).collect { event ->
        when (event) {
            is AiStreamEvent.Status -> onStatus(event.status)
            is AiStreamEvent.Reasoning -> {
                reasoning.append(event.delta)
                val now = System.nanoTime()
                if (now - lastReasoningFlush >= STREAM_UI_THROTTLE_NS) {
                    onReasoning(reasoning.toString())
                    lastReasoningFlush = now
                }
            }
            is AiStreamEvent.Content -> {
                raw.append(event.delta)
                val now = System.nanoTime()
                if (now - lastContentFlush >= STREAM_UI_THROTTLE_NS) {
                    onMarkdown(raw.toString())
                    lastContentFlush = now
                }
            }
            is AiStreamEvent.Media -> {
                appendMedia(raw, event.media)
                onMarkdown(raw.toString())
            }
            is AiStreamEvent.Done -> {
                onMarkdown(raw.toString())
                onReasoning(reasoning.toString())
                onStatus("")
            }
            is AiStreamEvent.Failure -> failure = event.message
        }
    }

    if (raw.isEmpty() && failure != null) {
        onStatus("流式连接不可用，正在兼容重试…")
        when (val fallback = ClientAiApi.ask(system, prompt, context = context)) {
            is AiGatewayResult.Success -> {
                raw.append(fallback.content)
                fallback.media.forEach { appendMedia(raw, it) }
                onMarkdown(raw.toString())
            }
            is AiGatewayResult.Failure -> failure = fallback.message
        }
    }

    onMarkdown(raw.toString())
    onReasoning(reasoning.toString())
    onStatus("")
    return CollectedReply(raw.toString(), failure)
}

private fun employeeSystemPrompt(employee: Employee): String? =
    employee.rolePrompt.trim().takeIf { it.isNotEmpty() }

private fun groupSystemPrompt(employee: Employee): String? =
    employee.rolePrompt.trim().takeIf { it.isNotEmpty() }
