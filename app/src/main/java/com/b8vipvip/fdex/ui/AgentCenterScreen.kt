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
    var loading by remember { mutableStateOf(false) }
    var githubToken by remember { mutableStateOf("") }
    var repository by remember { mutableStateOf("") }
    var projectName by remember { mutableStateOf("") }
    var baseBranch by remember { mutableStateOf("main") }
    var memoryMb by remember { mutableStateOf("2048") }
    var allowNetwork by remember { mutableStateOf(false) }

    suspend fun reloadProjects() {
        if (accessToken.isBlank()) return
        when (val result = AgentApi.listProjects(accessToken)) {
            is AgentApiResult.Success -> projects = result.value
            is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
        }
    }

    LaunchedEffect(accessToken) { reloadProjects() }

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
                    Text("Coding Agent、GitHub 项目和沙箱都绑定当前 FDEX user_id，不再使用第二套 Agent 账号。", color = Muted)
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
                                when (val connection = AgentApi.saveGitHubConnection(accessToken, githubToken.trim())) {
                                    is AgentApiResult.Failure -> snackbar.showSnackbar(connection.message)
                                    is AgentApiResult.Success -> {
                                        val repoName = repository.trim()
                                        when (
                                            val saved = AgentApi.saveProject(
                                                accessToken = accessToken,
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
