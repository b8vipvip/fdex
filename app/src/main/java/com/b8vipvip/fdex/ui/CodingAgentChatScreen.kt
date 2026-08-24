package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AgentEmployeePreferences
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.network.AgentApi
import com.b8vipvip.fdex.network.AgentApiResult
import com.b8vipvip.fdex.network.AgentProjectDto
import com.b8vipvip.fdex.network.AgentTaskDto
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
internal fun CodingAgentChatScreen(
    repo: AppRepository,
    employeeId: Long,
    revision: Int,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val employee = repo.employee(employeeId) ?: return
    val agentPrefs = remember { AgentEmployeePreferences(context) }
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    var text by remember { mutableStateOf("") }
    var token by remember { mutableStateOf(agentPrefs.accessToken()) }
    var editingToken by remember { mutableStateOf(token.isBlank()) }
    var busy by remember { mutableStateOf(false) }
    var liveTask by remember { mutableStateOf<AgentTaskDto?>(null) }
    var projects by remember { mutableStateOf<List<AgentProjectDto>>(emptyList()) }
    var selectedProjectId by remember { mutableStateOf(agentPrefs.projectId(employeeId)) }

    LaunchedEffect(token, editingToken) {
        if (token.isNotBlank() && !editingToken) {
            when (val result = AgentApi.listProjects(token)) {
                is AgentApiResult.Success -> {
                    projects = result.value
                    if (selectedProjectId == null || projects.none { it.id == selectedProjectId }) {
                        selectedProjectId = projects.firstOrNull()?.id
                        agentPrefs.setProjectId(employeeId, selectedProjectId)
                    }
                }
                is AgentApiResult.Failure -> Unit
            }
        }
    }

    LaunchedEffect(revision, liveTask?.events?.size, busy) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        if (editingToken) {
            Card(Modifier.fillMaxWidth().padding(12.dp)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Coding Agent 访问令牌", fontWeight = FontWeight.Bold)
                    Text("这里只配置 FDEX Agent Token。AI 接口统一使用服务端“供应商管理”；GitHub Token 也只在服务端控制台配置。", color = Muted, style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(value = token, onValueChange = { token = it }, label = { Text("FDEX Agent Token") }, visualTransformation = PasswordVisualTransformation(), singleLine = true, modifier = Modifier.fillMaxWidth())
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(enabled = token.isNotBlank(), onClick = { agentPrefs.setAccessToken(token); editingToken = false }) { Text("保存") }
                        if (agentPrefs.accessToken().isNotBlank()) OutlinedButton(onClick = { token = agentPrefs.accessToken(); editingToken = false }) { Text("取消") }
                    }
                }
            }
        }

        LazyColumn(state = listState, modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("💻 Coding Agent · ${employee.name}", fontWeight = FontWeight.Bold)
                        Text("AI：统一供应商模型池", color = Emerald)
                        val selected = projects.firstOrNull { it.id == selectedProjectId }
                        Text("项目：${selected?.name ?: "未选择"}${selected?.repository?.let { " · $it" } ?: ""}", color = Muted)
                        if (projects.isEmpty()) {
                            Text("请先在服务端控制台 → Coding Agent 添加 GitHub 项目；未配置项目时仍可兼容使用本机 FDEX 工作区。", style = MaterialTheme.typography.bodySmall, color = Muted)
                        } else {
                            projects.forEach { project ->
                                OutlinedButton(
                                    enabled = !busy,
                                    onClick = { selectedProjectId = project.id; agentPrefs.setProjectId(employeeId, project.id) },
                                ) {
                                    val marker = if (project.id == selectedProjectId) "✓ " else ""
                                    Text("$marker${project.name} · ${project.baseBranch}${if (project.allowPr) " · PR" else ""}")
                                }
                            }
                        }
                        OutlinedButton(onClick = { editingToken = true }) { Text("修改 Agent Token") }
                    }
                }
            }
            items(repo.messages(employeeId), key = { it.id }) { message ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(if (message.role == "user") "你" else employee.name, fontWeight = FontWeight.SemiBold)
                        Text(message.content, modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }
            if (busy) {
                item(key = "agent-progress-$employeeId") {
                    val task = liveTask
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                            Text("Agent 正在执行 · ${task?.projectName ?: "准备项目"}", fontWeight = FontWeight.Bold)
                            Text(task?.status?.ifBlank { "starting" } ?: "starting", color = Emerald)
                            task?.repository?.takeIf { it.isNotBlank() }?.let { Text("仓库：$it", color = Muted) }
                            task?.branch?.takeIf { it.isNotBlank() }?.let { Text("分支：$it", color = Muted) }
                            task?.events?.takeLast(8)?.forEach { event -> Text("• ${event.message}", style = MaterialTheme.typography.bodySmall) }
                        }
                    }
                }
            }
        }

        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(value = text, onValueChange = { text = it }, label = { Text("给 Coding Agent 安排开发任务") }, modifier = Modifier.weight(1f), enabled = !busy, minLines = 1, maxLines = 5)
            Button(
                enabled = text.isNotBlank() && !busy && !editingToken,
                onClick = {
                    val prompt = text.trim(); val accessToken = agentPrefs.accessToken()
                    if (accessToken.isBlank()) { editingToken = true; scope.launch { snackbar.showSnackbar("请先配置 FDEX Agent Token") }; return@Button }
                    text = ""; repo.addMessage(employeeId, "user", prompt); onChanged(); busy = true; liveTask = null
                    scope.launch {
                        when (val created = AgentApi.createTask(accessToken, prompt, selectedProjectId)) {
                            is AgentApiResult.Failure -> { busy = false; snackbar.showSnackbar(created.message) }
                            is AgentApiResult.Success -> {
                                liveTask = created.value
                                val runner = async { AgentApi.runTask(accessToken, created.value.id) }
                                while (runner.isActive) {
                                    delay(1000)
                                    when (val polled = AgentApi.getTask(accessToken, created.value.id)) { is AgentApiResult.Success -> liveTask = polled.value; is AgentApiResult.Failure -> Unit }
                                }
                                when (val completed = runner.await()) {
                                    is AgentApiResult.Failure -> repo.addMessage(employeeId, "employee", "Coding Agent 执行失败：${liveTask?.error.orEmpty().ifBlank { completed.message }}")
                                    is AgentApiResult.Success -> { liveTask = completed.value; repo.addMessage(employeeId, "employee", formatAgentResult(completed.value)) }
                                }
                                busy = false; onChanged()
                            }
                        }
                    }
                },
            ) { Text(if (busy) "执行中" else "发送") }
        }
    }
}

internal fun formatAgentResult(task: AgentTaskDto): String {
    if (task.status != "succeeded") return "Coding Agent 执行失败：${task.error.ifBlank { "任务未成功完成" }}"
    return buildString {
        append(task.result.ifBlank { "开发任务已完成。" })
        if (task.projectName.isNotBlank()) append("\n\n项目：${task.projectName}")
        if (task.repository.isNotBlank()) append("\n仓库：${task.repository}")
        if (task.branch.isNotBlank()) append("\n分支：${task.branch}")
        if (task.commitSha.isNotBlank()) append("\nCommit：${task.commitSha}")
        if (task.pushed) append("\n已推送 Agent 分支")
        if (task.prUrl.isNotBlank()) append("\nPR：${task.prUrl}")
        if (task.changedFiles.isNotEmpty()) append("\n修改文件：${task.changedFiles.joinToString(", ")}")
    }
}
