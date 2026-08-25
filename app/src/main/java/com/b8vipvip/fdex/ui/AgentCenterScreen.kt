package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AgentEmployeePreferences
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.CentralSessionStore
import com.b8vipvip.fdex.network.AgentApi
import com.b8vipvip.fdex.network.AgentApiResult
import com.b8vipvip.fdex.network.AgentProjectDto
import com.b8vipvip.fdex.network.AgentSandboxUsageDto
import com.b8vipvip.fdex.network.AgentTaskDto
import kotlinx.coroutines.launch

@Composable
internal fun AgentCenterScreen(
    repo: AppRepository,
    revision: Int,
    onChanged: () -> Unit,
    onOpenEmployee: (Long) -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val agentPrefs = remember { AgentEmployeePreferences(context) }
    val scope = rememberCoroutineScope()
    val accessToken = sessions.accessToken()

    var projects by remember { mutableStateOf<List<AgentProjectDto>>(emptyList()) }
    var tasks by remember { mutableStateOf<List<AgentTaskDto>>(emptyList()) }
    var usage by remember { mutableStateOf<AgentSandboxUsageDto?>(null) }
    var loading by remember { mutableStateOf(false) }
    var operationsBusy by remember { mutableStateOf(false) }
    var githubToken by remember { mutableStateOf("") }
    var repository by remember { mutableStateOf("") }
    var projectName by remember { mutableStateOf("") }
    var baseBranch by remember { mutableStateOf("main") }
    var memoryMb by remember { mutableStateOf("2048") }
    var allowNetwork by remember { mutableStateOf(false) }

    suspend fun reloadProjects() {
        if (accessToken.isBlank()) return
        when (val result = AgentApi.listProjects(context)) {
            is AgentApiResult.Success -> projects = result.value
            is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
        }
    }

    suspend fun reloadOperations(showError: Boolean = false) {
        if (accessToken.isBlank()) return
        when (val result = AgentApi.listTasks(context, limit = 30)) {
            is AgentApiResult.Success -> tasks = result.value
            is AgentApiResult.Failure -> if (showError) snackbar.showSnackbar(result.message)
        }
        when (val result = AgentApi.sandboxUsage(context)) {
            is AgentApiResult.Success -> usage = result.value
            is AgentApiResult.Failure -> if (showError) snackbar.showSnackbar(result.message)
        }
    }

    LaunchedEffect(accessToken) {
        reloadProjects()
        reloadOperations()
    }

    LazyColumn(
        Modifier.fillMaxSize().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("💻 Coding Agent 中心", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("中心账号：${sessions.email().ifBlank { "未登录" }}", color = Emerald)
                    Text("User ID：${sessions.userId().ifBlank { "-" }}", color = Muted)
                    Text("Coding Agent、GitHub 项目、任务历史和沙箱都绑定当前 FDEX user_id。", color = Muted)
                }
            }
        }

        item { SectionTitle("任务与沙箱") }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("账号沙箱空间", fontWeight = FontWeight.Bold)
                    val current = usage
                    if (current == null) {
                        Text("正在读取沙箱空间…", color = Muted)
                    } else {
                        Text("已用 ${current.usedMb} MB / ${current.limitMb} MB（${current.percent}%）", color = if (current.overLimit) MaterialTheme.colorScheme.error else Muted)
                        Text("构建缓存 ${current.cacheMb} MB · 项目/任务 ${"%.1f".format(current.workspaceBytes / 1024.0 / 1024.0)} MB", color = Muted, style = MaterialTheme.typography.bodySmall)
                        if (current.overLimit) Text("已达到磁盘预算，新任务会暂停创建；先清理已完成任务空间。", color = MaterialTheme.colorScheme.error)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            enabled = !operationsBusy && accessToken.isNotBlank(),
                            onClick = { scope.launch { reloadOperations(showError = true) } },
                        ) { Text("刷新") }
                        Button(
                            enabled = !operationsBusy && accessToken.isNotBlank(),
                            onClick = {
                                operationsBusy = true
                                scope.launch {
                                    when (val result = AgentApi.cleanupSandbox(context)) {
                                        is AgentApiResult.Success -> {
                                            usage = result.value
                                            reloadOperations()
                                            snackbar.showSnackbar("已释放完成任务的 worktree 和构建缓存")
                                        }
                                        is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
                                    }
                                    operationsBusy = false
                                }
                            },
                        ) { Text(if (operationsBusy) "处理中…" else "清理已完成任务空间") }
                    }
                    Text("任务历史持久保存；清理只释放已完成/失败/取消任务的 worktree 和缓存，不删除 GitHub 仓库、Commit、PR 或任务记录。", color = Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        if (tasks.isEmpty()) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Text("当前账号还没有 Coding Agent 任务历史。", modifier = Modifier.padding(14.dp), color = Muted)
                }
            }
        } else {
            items(tasks, key = { "agent-history-${it.id}" }) { task ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(task.projectName.ifBlank { "Coding Agent" }, fontWeight = FontWeight.Bold)
                            Text(agentTaskStatusLabel(task.status), color = if (task.status == "failed") MaterialTheme.colorScheme.error else Emerald)
                        }
                        Text(task.prompt.take(240), style = MaterialTheme.typography.bodySmall)
                        task.repository.takeIf { it.isNotBlank() }?.let { Text(it, color = Muted, style = MaterialTheme.typography.bodySmall) }
                        task.prUrl.takeIf { it.isNotBlank() }?.let { Text("PR：$it", color = Emerald, style = MaterialTheme.typography.bodySmall) }
                        if (task.error.isNotBlank()) Text(task.error.take(300), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (task.status == "queued" || task.status == "running") {
                                OutlinedButton(
                                    enabled = !operationsBusy,
                                    onClick = {
                                        operationsBusy = true
                                        scope.launch {
                                            when (val result = AgentApi.cancelTask(context, task.id)) {
                                                is AgentApiResult.Success -> {
                                                    reloadOperations()
                                                    snackbar.showSnackbar("已请求停止 Coding Agent 任务")
                                                }
                                                is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
                                            }
                                            operationsBusy = false
                                        }
                                    },
                                ) { Text(if (task.cancelRequested) "停止中" else "停止") }
                            } else {
                                OutlinedButton(
                                    enabled = !operationsBusy,
                                    onClick = {
                                        operationsBusy = true
                                        scope.launch {
                                            when (val result = AgentApi.retryTask(context, task.id)) {
                                                is AgentApiResult.Success -> {
                                                    reloadOperations()
                                                    snackbar.showSnackbar("已创建重试任务，可进入 Coding Agent 继续执行")
                                                }
                                                is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
                                            }
                                            operationsBusy = false
                                        }
                                    },
                                ) { Text("重新执行") }
                            }
                        }
                    }
                }
            }
        }

        item { SectionTitle("启用 Coding Agent") }
        items(repo.employees(), key = { "agent-employee-${it.id}" }) { employee ->
            val enabled = agentPrefs.isCodingAgent(employee.id)
            Card(Modifier.fillMaxWidth()) {
                Row(
                    Modifier.fillMaxWidth().padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(employee.name, fontWeight = FontWeight.SemiBold)
                        Text(if (enabled) "已启用 · 点击员工后直接进入 Coding Agent" else "普通 AI 员工", color = Muted)
                    }
                    Switch(
                        checked = enabled,
                        onCheckedChange = {
                            agentPrefs.setCodingAgent(employee.id, it)
                            onChanged()
                            scope.launch { snackbar.showSnackbar(if (it) "${employee.name} 已启用 Coding Agent" else "${employee.name} 已恢复普通 AI") }
                        },
                    )
                    Button(onClick = { onOpenEmployee(employee.id) }) { Text("打开") }
                }
            }
        }

        item { SectionTitle("GitHub 与项目") }
        if (projects.isNotEmpty()) {
            items(projects, key = { "agent-project-${it.id}" }) { project ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(project.name, fontWeight = FontWeight.Bold)
                        Text(project.repository, color = Emerald)
                        Text("${project.baseBranch} · ${project.sandboxMemoryMb} MB · CPU ${project.sandboxCpuPercent}% · 网络${if (project.allowNetwork) "允许" else "隔离"}", color = Muted)
                    }
                }
            }
        } else {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Text("当前账号还没有 GitHub Agent 项目，可直接在下面添加。", modifier = Modifier.padding(14.dp), color = Muted)
                }
            }
        }

        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("连接 GitHub 并添加项目", fontWeight = FontWeight.Bold)
                    Text("GitHub Token 只提交到中心服务器加密保存，不写入 Android 本机数据库。", color = Muted)
                    OutlinedTextField(
                        githubToken,
                        { githubToken = it },
                        label = { Text("GitHub Token") },
                        visualTransformation = PasswordVisualTransformation(),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    OutlinedTextField(repository, { repository = it }, label = { Text("仓库，例如 b8vipvip/fdex") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(projectName, { projectName = it }, label = { Text("项目名称（可选）") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(baseBranch, { baseBranch = it }, label = { Text("基础分支") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(memoryMb, { memoryMb = it.filter(Char::isDigit) }, label = { Text("单任务内存上限 MB") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(allowNetwork, { allowNetwork = it })
                        Text("允许构建任务联网下载依赖")
                    }
                    Button(
                        enabled = !loading && accessToken.isNotBlank() && githubToken.isNotBlank() && repository.isNotBlank(),
                        onClick = {
                            loading = true
                            scope.launch {
                                when (val connection = AgentApi.saveGitHubConnection(context, githubToken.trim())) {
                                    is AgentApiResult.Failure -> snackbar.showSnackbar(connection.message)
                                    is AgentApiResult.Success -> {
                                        val repoName = repository.trim()
                                        when (
                                            val saved = AgentApi.saveProject(
                                                context = context,
                                                connectionId = connection.value.id,
                                                repository = repoName,
                                                name = projectName.trim().ifBlank { repoName.substringAfterLast('/') },
                                                baseBranch = baseBranch.trim().ifBlank { "main" },
                                                allowPush = true,
                                                allowPr = true,
                                                allowNetwork = allowNetwork,
                                                sandboxMemoryMb = memoryMb.toIntOrNull()?.coerceIn(128, 16384) ?: 2048,
                                            )
                                        ) {
                                            is AgentApiResult.Failure -> snackbar.showSnackbar(saved.message)
                                            is AgentApiResult.Success -> {
                                                githubToken = ""
                                                repository = ""
                                                projectName = ""
                                                reloadProjects()
                                                reloadOperations()
                                                snackbar.showSnackbar("GitHub 项目已加入当前 FDEX 账号")
                                            }
                                        }
                                    }
                                }
                                loading = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (loading) "连接中…" else "连接 GitHub 并添加项目") }
                }
            }
        }
    }
}

private fun agentTaskStatusLabel(status: String): String = when (status) {
    "queued" -> "等待执行"
    "running" -> "执行中"
    "succeeded" -> "已完成"
    "failed" -> "失败"
    "canceled" -> "已取消"
    else -> status.ifBlank { "未知" }
}
