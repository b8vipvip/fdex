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
import androidx.compose.material3.Checkbox
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
    var bootstrapToken by remember { mutableStateOf(agentPrefs.accessToken()) }
    var accountId by remember { mutableStateOf(agentPrefs.accountId()) }
    var accountToken by remember { mutableStateOf(agentPrefs.accountToken()) }
    var editingBootstrap by remember { mutableStateOf(accountToken.isBlank() && bootstrapToken.isBlank()) }
    var enrollmentBusy by remember { mutableStateOf(false) }

    var busy by remember { mutableStateOf(false) }
    var liveTask by remember { mutableStateOf<AgentTaskDto?>(null) }
    var projects by remember { mutableStateOf<List<AgentProjectDto>>(emptyList()) }
    var selectedProjectId by remember { mutableStateOf(agentPrefs.projectId(employeeId)) }

    var showProjectSetup by remember { mutableStateOf(false) }
    var setupBusy by remember { mutableStateOf(false) }
    var githubToken by remember { mutableStateOf("") }
    var repositoryName by remember { mutableStateOf("") }
    var projectName by remember { mutableStateOf("") }
    var baseBranch by remember { mutableStateOf("main") }
    var memoryMb by remember { mutableStateOf("2048") }
    var allowNetwork by remember { mutableStateOf(false) }

    suspend fun reloadProjects(token: String) {
        when (val result = AgentApi.listProjects(token)) {
            is AgentApiResult.Success -> {
                projects = result.value
                if (selectedProjectId == null || projects.none { it.id == selectedProjectId }) {
                    selectedProjectId = projects.firstOrNull()?.id
                    agentPrefs.setProjectId(employeeId, selectedProjectId)
                }
            }
            is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
        }
    }

    LaunchedEffect(accountToken) {
        if (accountToken.isNotBlank()) reloadProjects(accountToken)
    }

    LaunchedEffect(revision, liveTask?.events?.size, busy) {
        val last = listState.layoutInfo.totalItemsCount - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    Column(Modifier.fillMaxSize()) {
        if (editingBootstrap) {
            Card(Modifier.fillMaxWidth().padding(12.dp)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("注册 Coding Agent 账号空间", fontWeight = FontWeight.Bold)
                    Text(
                        "此令牌只用于首次注册。注册后服务端会签发本账号独立凭据，GitHub、项目和沙箱都会绑定到该账号；AI 仍统一使用供应商管理。",
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    OutlinedTextField(
                        value = bootstrapToken,
                        onValueChange = { bootstrapToken = it },
                        label = { Text("FDEX Agent 注册令牌") },
                        visualTransformation = PasswordVisualTransformation(),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            enabled = bootstrapToken.isNotBlank() && !enrollmentBusy,
                            onClick = {
                                enrollmentBusy = true
                                scope.launch {
                                    when (val enrolled = AgentApi.enrollAccount(bootstrapToken, "Android · ${employee.name}")) {
                                        is AgentApiResult.Failure -> snackbar.showSnackbar(enrolled.message)
                                        is AgentApiResult.Success -> {
                                            accountId = enrolled.value.ownerId
                                            accountToken = enrolled.value.accountToken
                                            agentPrefs.setAccountCredential(accountId, accountToken)
                                            // Do not retain the server-wide enrollment secret after account provisioning.
                                            agentPrefs.setAccessToken("")
                                            bootstrapToken = ""
                                            editingBootstrap = false
                                            reloadProjects(accountToken)
                                            snackbar.showSnackbar("Coding Agent 账号空间已创建")
                                        }
                                    }
                                    enrollmentBusy = false
                                }
                            },
                        ) { Text(if (enrollmentBusy) "注册中" else "注册账号空间") }
                        if (accountToken.isNotBlank()) {
                            OutlinedButton(onClick = { editingBootstrap = false }) { Text("取消") }
                        }
                    }
                }
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("💻 Coding Agent · ${employee.name}", fontWeight = FontWeight.Bold)
                        Text("AI：统一供应商模型池", color = Emerald)
                        Text("账号沙箱：${accountId.ifBlank { "尚未注册" }}", color = Muted)
                        val selected = projects.firstOrNull { it.id == selectedProjectId }
                        Text("项目：${selected?.name ?: "未选择"}${selected?.repository?.let { " · $it" } ?: ""}", color = Muted)
                        selected?.let {
                            Text(
                                "执行限制：${it.sandboxMemoryMb} MB · CPU ${it.sandboxCpuPercent}% · 网络${if (it.allowNetwork) "允许" else "隔离"}",
                                style = MaterialTheme.typography.bodySmall,
                                color = Muted,
                            )
                        }
                        if (projects.isNotEmpty()) {
                            projects.forEach { project ->
                                OutlinedButton(
                                    enabled = !busy,
                                    onClick = {
                                        selectedProjectId = project.id
                                        agentPrefs.setProjectId(employeeId, project.id)
                                    },
                                ) {
                                    val marker = if (project.id == selectedProjectId) "✓ " else ""
                                    Text("$marker${project.name} · ${project.baseBranch}${if (project.allowPr) " · PR" else ""}")
                                }
                            }
                        } else if (accountToken.isNotBlank()) {
                            Text("这个 FDEX 账号还没有 GitHub 项目。", style = MaterialTheme.typography.bodySmall, color = Muted)
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                enabled = accountToken.isNotBlank() && !busy,
                                onClick = { showProjectSetup = !showProjectSetup },
                            ) { Text(if (showProjectSetup) "收起 GitHub 配置" else "添加 GitHub 项目") }
                            OutlinedButton(
                                enabled = !busy,
                                onClick = {
                                    agentPrefs.clearAccountCredential()
                                    accountId = ""
                                    accountToken = ""
                                    projects = emptyList()
                                    selectedProjectId = null
                                    editingBootstrap = true
                                },
                            ) { Text("切换账号空间") }
                        }
                    }
                }
            }

            if (showProjectSetup && accountToken.isNotBlank()) {
                item(key = "github-project-setup") {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("GitHub · 当前 FDEX 账号独立配置", fontWeight = FontWeight.Bold)
                            Text(
                                "GitHub Token 只提交到服务端加密保存，不会写入手机；其它 FDEX 账号无法读取此连接、项目或沙箱目录。",
                                style = MaterialTheme.typography.bodySmall,
                                color = Muted,
                            )
                            OutlinedTextField(
                                value = githubToken,
                                onValueChange = { githubToken = it },
                                label = { Text("GitHub Token") },
                                visualTransformation = PasswordVisualTransformation(),
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = repositoryName,
                                onValueChange = { repositoryName = it },
                                label = { Text("仓库，例如 b8vipvip/fdex") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = projectName,
                                onValueChange = { projectName = it },
                                label = { Text("项目名称（可选）") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = baseBranch,
                                onValueChange = { baseBranch = it },
                                label = { Text("基础分支") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                value = memoryMb,
                                onValueChange = { memoryMb = it.filter(Char::isDigit) },
                                label = { Text("单任务内存上限 MB") },
                                singleLine = true,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                Checkbox(checked = allowNetwork, onCheckedChange = { allowNetwork = it })
                                Text("允许构建任务联网下载依赖（默认关闭更安全）")
                            }
                            Button(
                                enabled = githubToken.isNotBlank() && repositoryName.isNotBlank() && !setupBusy,
                                onClick = {
                                    setupBusy = true
                                    scope.launch {
                                        when (val connection = AgentApi.saveGitHubConnection(accountToken, githubToken)) {
                                            is AgentApiResult.Failure -> snackbar.showSnackbar(connection.message)
                                            is AgentApiResult.Success -> {
                                                val memory = memoryMb.toIntOrNull()?.coerceIn(128, 16384) ?: 2048
                                                val shortName = repositoryName.substringAfterLast('/').ifBlank { "GitHub Project" }
                                                when (
                                                    val saved = AgentApi.saveProject(
                                                        accountToken = accountToken,
                                                        connectionId = connection.value.id,
                                                        repository = repositoryName.trim(),
                                                        name = projectName.trim().ifBlank { shortName },
                                                        baseBranch = baseBranch.trim().ifBlank { "main" },
                                                        allowPush = true,
                                                        allowPr = true,
                                                        allowNetwork = allowNetwork,
                                                        sandboxMemoryMb = memory,
                                                    )
                                                ) {
                                                    is AgentApiResult.Failure -> snackbar.showSnackbar(saved.message)
                                                    is AgentApiResult.Success -> {
                                                        githubToken = ""
                                                        repositoryName = ""
                                                        projectName = ""
                                                        selectedProjectId = saved.value.id
                                                        agentPrefs.setProjectId(employeeId, saved.value.id)
                                                        reloadProjects(accountToken)
                                                        showProjectSetup = false
                                                        snackbar.showSnackbar("GitHub 项目已加入当前账号沙箱")
                                                    }
                                                }
                                            }
                                        }
                                        setupBusy = false
                                    }
                                },
                            ) { Text(if (setupBusy) "连接中" else "连接 GitHub 并添加项目") }
                        }
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
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                label = { Text("给 Coding Agent 安排开发任务") },
                modifier = Modifier.weight(1f),
                enabled = !busy,
                minLines = 1,
                maxLines = 5,
            )
            Button(
                enabled = text.isNotBlank() && !busy && !editingBootstrap && accountToken.isNotBlank(),
                onClick = {
                    val prompt = text.trim()
                    if (accountToken.isBlank()) {
                        editingBootstrap = true
                        scope.launch { snackbar.showSnackbar("请先注册 Coding Agent 账号空间") }
                        return@Button
                    }
                    text = ""
                    repo.addMessage(employeeId, "user", prompt)
                    onChanged()
                    busy = true
                    liveTask = null
                    scope.launch {
                        when (val created = AgentApi.createTask(accountToken, prompt, selectedProjectId)) {
                            is AgentApiResult.Failure -> {
                                busy = false
                                snackbar.showSnackbar(created.message)
                            }
                            is AgentApiResult.Success -> {
                                liveTask = created.value
                                val runner = async { AgentApi.runTask(accountToken, created.value.id) }
                                while (runner.isActive) {
                                    delay(1000)
                                    when (val polled = AgentApi.getTask(accountToken, created.value.id)) {
                                        is AgentApiResult.Success -> liveTask = polled.value
                                        is AgentApiResult.Failure -> Unit
                                    }
                                }
                                when (val completed = runner.await()) {
                                    is AgentApiResult.Failure -> repo.addMessage(
                                        employeeId,
                                        "employee",
                                        "Coding Agent 执行失败：${liveTask?.error.orEmpty().ifBlank { completed.message }}",
                                    )
                                    is AgentApiResult.Success -> {
                                        liveTask = completed.value
                                        repo.addMessage(employeeId, "employee", formatAgentResult(completed.value))
                                    }
                                }
                                busy = false
                                onChanged()
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
