package com.b8vipvip.fdex.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.GroupMessage
import com.b8vipvip.fdex.data.isPrivateAssistant
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.AiStreamEvent
import com.b8vipvip.fdex.network.ClientAiApi
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.launch

private const val STREAM_UI_THROTTLE_NS = 50_000_000L

@Composable
internal fun StreamingEmployeeChatScreen(
    repo: AppRepository,
    employeeId: Long,
    revision: Int,
    onChanged: () -> Unit,
    onOpenManage: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val employee = repo.employee(employeeId) ?: return
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    var streamMarkdown by remember { mutableStateOf("") }
    var streamStatus by remember { mutableStateOf("") }
    var streamReasoning by remember { mutableStateOf("") }

    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.scrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Avatar(employeeEmoji(employee))
            Column(Modifier.weight(1f).padding(start = 10.dp)) {
                Text("${employee.name} · ${employee.position}", fontWeight = FontWeight.SemiBold)
                Text(employee.department, color = Muted)
            }
            Box {
                TextButton(onClick = { menu = true }) { Text("•••") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("员工管理") }, onClick = { menu = false; onOpenManage() })
                    DropdownMenuItem(
                        text = { Text("清空聊天记录") },
                        onClick = { repo.clearMessages(employeeId); menu = false; onChanged() },
                    )
                }
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(repo.messages(employeeId), key = { it.id }) { message ->
                StreamingEmployeeBubble(message, employee)
            }
            if (busy) {
                item(key = "streaming-$employeeId") {
                    LiveAssistantBubble(
                        employee = employee,
                        markdown = streamMarkdown,
                        status = streamStatus,
                        reasoning = streamReasoning,
                    )
                }
            }
        }

        Row(
            Modifier.fillMaxWidth().background(Color.White).padding(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                placeholder = { Text("给员工安排任务…") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                enabled = text.isNotBlank() && !busy,
                onClick = {
                    val prompt = text.trim()
                    text = ""
                    repo.addMessage(employeeId, "user", prompt)
                    onChanged()
                    busy = true
                    streamMarkdown = ""
                    streamStatus = "正在连接 AI…"
                    streamReasoning = ""
                    scope.launch {
                        val system = employeeSystemPrompt(employee)
                        val raw = StringBuilder()
                        val reasoning = StringBuilder()
                        var lastFlush = 0L
                        var failure: String? = null

                        ClientAiApi.streamAsk(system, prompt).collect { event ->
                            when (event) {
                                is AiStreamEvent.Status -> streamStatus = event.status
                                is AiStreamEvent.Reasoning -> {
                                    reasoning.append(event.delta)
                                    val now = System.nanoTime()
                                    if (now - lastFlush >= STREAM_UI_THROTTLE_NS) {
                                        streamReasoning = reasoning.toString()
                                        lastFlush = now
                                    }
                                }
                                is AiStreamEvent.Content -> {
                                    raw.append(event.delta)
                                    val now = System.nanoTime()
                                    if (now - lastFlush >= STREAM_UI_THROTTLE_NS) {
                                        streamMarkdown = raw.toString()
                                        lastFlush = now
                                    }
                                }
                                is AiStreamEvent.Done -> {
                                    streamMarkdown = raw.toString()
                                    streamReasoning = reasoning.toString()
                                    streamStatus = ""
                                }
                                is AiStreamEvent.Failure -> failure = event.message
                            }
                        }

                        if (raw.isEmpty() && failure != null) {
                            streamStatus = "流式连接不可用，正在兼容重试…"
                            when (val fallback = ClientAiApi.ask(system, prompt)) {
                                is AiGatewayResult.Success -> raw.append(fallback.content)
                                is AiGatewayResult.Failure -> failure = fallback.message
                            }
                        }

                        if (raw.isNotEmpty()) {
                            repo.addMessage(employeeId, "employee", raw.toString())
                        } else {
                            val message = failure ?: "AI 没有返回正文内容"
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
            ) { Text("发送") }
        }
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
        if (last >= 0) listState.scrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        Card(Modifier.fillMaxWidth().padding(10.dp)) {
            Column(Modifier.padding(12.dp)) {
                Text("👥 ${group.name}", fontWeight = FontWeight.Bold)
                Text("${members.size} 名成员 · ${if (group.autoMode) "自动运营" else "人工指挥"}", color = Emerald)
                Text(group.description, color = Muted)
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(repo.groupMessages(groupId), key = { it.id }) { message -> StreamingGroupBubble(message) }
            if (busy) {
                item(key = "streaming-group-$groupId") {
                    liveEmployee?.let {
                        LiveAssistantBubble(it, streamMarkdown, streamStatus, streamReasoning)
                    } ?: Text(streamStatus.ifBlank { "团队正在处理…" }, color = Muted)
                }
            }
        }

        Row(
            Modifier.fillMaxWidth().background(Color.White).padding(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                placeholder = { Text("@员工 或安排团队任务…") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                enabled = text.isNotBlank() && !busy,
                onClick = {
                    val prompt = text.trim()
                    text = ""
                    repo.addGroupMessage(groupId, "user", "我", prompt)
                    onChanged()
                    val target = members.firstOrNull { prompt.contains("@${it.name}") || prompt.contains(it.position) }
                        ?: members.firstOrNull()
                    if (target == null) {
                        repo.addGroupMessage(groupId, "system", "", "当前群里还没有 AI 员工。")
                        onChanged()
                        return@Button
                    }

                    busy = true
                    liveEmployee = target
                    streamMarkdown = ""
                    streamStatus = "${target.name} 正在连接 AI…"
                    streamReasoning = ""
                    scope.launch {
                        val system = groupSystemPrompt(target)
                        val raw = StringBuilder()
                        val reasoning = StringBuilder()
                        var lastFlush = 0L
                        var failure: String? = null

                        ClientAiApi.streamAsk(system, prompt).collect { event ->
                            when (event) {
                                is AiStreamEvent.Status -> streamStatus = event.status
                                is AiStreamEvent.Reasoning -> {
                                    reasoning.append(event.delta)
                                    val now = System.nanoTime()
                                    if (now - lastFlush >= STREAM_UI_THROTTLE_NS) {
                                        streamReasoning = reasoning.toString()
                                        lastFlush = now
                                    }
                                }
                                is AiStreamEvent.Content -> {
                                    raw.append(event.delta)
                                    val now = System.nanoTime()
                                    if (now - lastFlush >= STREAM_UI_THROTTLE_NS) {
                                        streamMarkdown = raw.toString()
                                        lastFlush = now
                                    }
                                }
                                is AiStreamEvent.Done -> {
                                    streamMarkdown = raw.toString()
                                    streamReasoning = reasoning.toString()
                                    streamStatus = ""
                                }
                                is AiStreamEvent.Failure -> failure = event.message
                            }
                        }

                        if (raw.isEmpty() && failure != null) {
                            streamStatus = "流式连接不可用，正在兼容重试…"
                            when (val fallback = ClientAiApi.ask(system, prompt)) {
                                is AiGatewayResult.Success -> raw.append(fallback.content)
                                is AiGatewayResult.Failure -> failure = fallback.message
                            }
                        }

                        val reply = if (raw.isNotEmpty()) raw.toString() else "暂时无法完成：${failure ?: "AI 没有返回正文内容"}"
                        repo.addGroupMessage(groupId, "employee", target.name, reply)
                        if (raw.isEmpty()) snackbar.showSnackbar(failure ?: "AI 没有返回正文内容")
                        busy = false
                        liveEmployee = null
                        streamMarkdown = ""
                        streamStatus = ""
                        streamReasoning = ""
                        onChanged()
                    }
                },
            ) { Text("发送") }
        }
    }
}

private fun employeeSystemPrompt(employee: Employee): String? = if (employee.isPrivateAssistant()) {
    null
} else {
    "你是 FDEX AI 虚拟公司的员工：${employee.name}，职位：${employee.position}，部门：${employee.department}。${employee.rolePrompt}。像真实同事一样简洁、主动、可执行地回答。"
}

private fun groupSystemPrompt(employee: Employee): String? = if (employee.isPrivateAssistant()) {
    null
} else {
    "你是工作群里的${employee.position} ${employee.name}。${employee.rolePrompt}。从团队协作角度简洁、可执行地回复。"
}

@Composable
private fun StreamingEmployeeBubble(message: ChatMessage, employee: Employee) {
    val isUser = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        if (!isUser) {
            Avatar(employeeEmoji(employee), 36)
            Spacer(Modifier.width(8.dp))
        }
        Column(
            horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
            modifier = Modifier.fillMaxWidth(.82f),
        ) {
            Text(if (isUser) "我" else employee.name, color = Muted)
            Card {
                if (isUser) {
                    Text(message.content, modifier = Modifier.padding(12.dp), color = Emerald)
                } else {
                    MarkdownText(message.content, modifier = Modifier.padding(12.dp))
                }
            }
        }
    }
}

@Composable
private fun LiveAssistantBubble(employee: Employee, markdown: String, status: String, reasoning: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Start) {
        Avatar(employeeEmoji(employee), 36)
        Spacer(Modifier.width(8.dp))
        Column(Modifier.fillMaxWidth(.82f)) {
            Text(employee.name, color = Muted)
            Card {
                Column(Modifier.padding(12.dp)) {
                    if (status.isNotBlank()) Text("🤔 $status", color = Muted)
                    if (reasoning.isNotBlank()) {
                        Text(
                            "🧠 ${reasoning.takeLast(800)}",
                            color = Muted,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(top = 4.dp, bottom = 6.dp),
                        )
                    }
                    if (markdown.isNotBlank()) MarkdownText(markdown)
                    else if (status.isBlank()) Text("正在等待正文…", color = Muted)
                }
            }
        }
    }
}

@Composable
private fun StreamingGroupBubble(message: GroupMessage) {
    val user = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) {
        Card(Modifier.fillMaxWidth(.84f)) {
            Column(Modifier.padding(12.dp)) {
                if (!user && message.employeeName.isNotBlank()) {
                    Text(message.employeeName, color = Emerald, style = MaterialTheme.typography.labelSmall)
                }
                if (user || message.role == "system") {
                    Text(message.content, color = if (user) Emerald else MaterialTheme.colorScheme.onSurface)
                } else {
                    MarkdownText(message.content)
                }
            }
        }
    }
}
