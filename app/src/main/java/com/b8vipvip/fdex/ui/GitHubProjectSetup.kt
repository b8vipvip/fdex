package com.b8vipvip.fdex.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.network.AgentApi
import com.b8vipvip.fdex.network.AgentApiResult
import com.b8vipvip.fdex.network.AgentGitHubConnectionDto
import com.b8vipvip.fdex.network.AgentGitHubDeviceFlowDto
import com.b8vipvip.fdex.network.AgentGitHubRepositoryDto
import com.b8vipvip.fdex.network.AgentProjectDto
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch


@Composable
internal fun GitHubProjectSetup(
    snackbar: SnackbarHostState,
    onProjectSaved: (AgentProjectDto) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var connections by remember { mutableStateOf<List<AgentGitHubConnectionDto>>(emptyList()) }
    var selectedConnectionId by remember { mutableStateOf<Int?>(null) }
    var repositories by remember { mutableStateOf<List<AgentGitHubRepositoryDto>>(emptyList()) }
    var selectedRepository by remember { mutableStateOf<String?>(null) }
    var deviceFlow by remember { mutableStateOf<AgentGitHubDeviceFlowDto?>(null) }
    var polling by remember { mutableStateOf(false) }
    var connectBusy by remember { mutableStateOf(false) }
    var repositoryBusy by remember { mutableStateOf(false) }
    var saveBusy by remember { mutableStateOf(false) }
    var repositoryQuery by remember { mutableStateOf("") }
    var projectName by remember { mutableStateOf("") }
    var baseBranch by remember { mutableStateOf("main") }
    var memoryMb by remember { mutableStateOf("2048") }
    var allowNetwork by remember { mutableStateOf(false) }

    suspend fun loadRepositories(connectionId: Int, showError: Boolean = true) {
        repositoryBusy = true
        when (val result = AgentApi.listGitHubRepositories(context, connectionId, repositoryQuery)) {
            is AgentApiResult.Success -> {
                repositories = result.value
                val current = result.value.firstOrNull { it.fullName == selectedRepository }
                if (current == null) selectedRepository = null
            }
            is AgentApiResult.Failure -> if (showError) snackbar.showSnackbar(result.message)
        }
        repositoryBusy = false
    }

    suspend fun refreshConnections(showError: Boolean = true) {
        when (val result = AgentApi.listGitHubConnections(context)) {
            is AgentApiResult.Success -> {
                connections = result.value
                val selected = result.value.firstOrNull { it.id == selectedConnectionId && !it.needsReconnect }
                    ?: result.value.firstOrNull { !it.needsReconnect }
                selectedConnectionId = selected?.id
                if (selected != null) loadRepositories(selected.id, showError)
            }
            is AgentApiResult.Failure -> if (showError) snackbar.showSnackbar(result.message)
        }
    }

    fun openDeviceAuthorization(flow: AgentGitHubDeviceFlowDto) {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("GitHub Device Code", flow.userCode))
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(flow.verificationUri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }.onFailure {
            scope.launch { snackbar.showSnackbar("无法打开浏览器，请手动访问 ${flow.verificationUri}") }
        }
    }

    LaunchedEffect(Unit) { refreshConnections(showError = false) }
    LaunchedEffect(deviceFlow?.id, polling) {
        if (!polling) return@LaunchedEffect
        while (polling) {
            val current = deviceFlow ?: break
            val waitSeconds = maxOf(1, current.retryAfterSeconds, current.intervalSeconds)
            delay(waitSeconds * 1000L)
            when (val result = AgentApi.pollGitHubDeviceFlow(context, current.id)) {
                is AgentApiResult.Failure -> {
                    snackbar.showSnackbar(result.message)
                    polling = false
                }
                is AgentApiResult.Success -> {
                    deviceFlow = result.value
                    when (result.value.status) {
                        "pending" -> Unit
                        "authorized" -> {
                            val connection = result.value.connection
                            if (connection != null) {
                                connections = (connections.filterNot { it.id == connection.id } + connection).sortedBy { it.id }
                                selectedConnectionId = connection.id
                                repositoryQuery = ""
                                loadRepositories(connection.id)
                                snackbar.showSnackbar("GitHub 已连接：${connection.login}")
                            } else {
                                refreshConnections()
                            }
                            polling = false
                        }
                        "denied" -> {
                            snackbar.showSnackbar("GitHub 授权已取消")
                            polling = false
                        }
                        "expired" -> {
                            snackbar.showSnackbar("GitHub 授权码已过期，请重新连接")
                            polling = false
                        }
                        else -> {
                            snackbar.showSnackbar(result.value.error.ifBlank { "GitHub 授权失败" })
                            polling = false
                        }
                    }
                }
            }
        }
    }

    Card(modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("GitHub Device OAuth 与项目", fontWeight = FontWeight.Bold)
            Text(
                "浏览器授权绑定当前 FDEX 账号；GitHub 凭据只在服务端加密保存，Android 不接收也不保存访问令牌。",
                color = Muted,
                style = MaterialTheme.typography.bodySmall,
            )

            if (connections.isNotEmpty()) {
                Text("GitHub 账号", fontWeight = FontWeight.SemiBold)
                connections.forEach { connection ->
                    OutlinedButton(
                        enabled = !repositoryBusy && !connection.needsReconnect,
                        onClick = {
                            selectedConnectionId = connection.id
                            selectedRepository = null
                            repositoryQuery = ""
                            scope.launch { loadRepositories(connection.id) }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        val marker = if (selectedConnectionId == connection.id) "✓ " else ""
                        val state = if (connection.needsReconnect) " · 需重新授权" else ""
                        Text("$marker${connection.login.ifBlank { connection.name }}$state")
                    }
                }
            }

            Button(
                enabled = !connectBusy && !polling,
                onClick = {
                    connectBusy = true
                    scope.launch {
                        when (val result = AgentApi.startGitHubDeviceFlow(context)) {
                            is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
                            is AgentApiResult.Success -> {
                                deviceFlow = result.value
                                polling = true
                                openDeviceAuthorization(result.value)
                                snackbar.showSnackbar("授权码 ${result.value.userCode} 已复制，请在 GitHub 页面确认")
                            }
                        }
                        connectBusy = false
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (connectBusy) "正在获取授权码…" else if (connections.isEmpty()) "连接 GitHub" else "连接另一个 GitHub 账号") }

            deviceFlow?.takeIf { it.status == "pending" }?.let { flow ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("GitHub 授权码：${flow.userCode}", fontWeight = FontWeight.Bold, color = Emerald)
                        Text("等待你在浏览器确认，FDEX 会按 GitHub 要求的频率自动检查。", color = Muted)
                        OutlinedButton(onClick = { openDeviceAuthorization(flow) }) { Text("复制授权码并打开 GitHub") }
                    }
                }
            }

            selectedConnectionId?.let { connectionId ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = repositoryQuery,
                        onValueChange = { repositoryQuery = it },
                        label = { Text("搜索有权访问的仓库") },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    OutlinedButton(
                        enabled = !repositoryBusy,
                        onClick = { scope.launch { loadRepositories(connectionId) } },
                    ) { Text(if (repositoryBusy) "读取中" else "搜索") }
                }

                if (!repositoryBusy && repositories.isEmpty()) {
                    Text("没有找到当前 GitHub 账号可访问的仓库。", color = Muted)
                }
                repositories.take(30).forEach { repository ->
                    OutlinedButton(
                        enabled = !repositoryBusy && !repository.archived,
                        onClick = {
                            selectedRepository = repository.fullName
                            projectName = repository.name
                            baseBranch = repository.defaultBranch
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        val marker = if (selectedRepository == repository.fullName) "✓ " else ""
                        val visibility = if (repository.isPrivate) "私有" else "公开"
                        val permission = if (repository.canPush) "可创建 PR" else "只读"
                        Text("$marker${repository.fullName} · $visibility · $permission")
                    }
                }
            }

            val chosen = repositories.firstOrNull { it.fullName == selectedRepository }
            if (chosen != null) {
                Text("已选：${chosen.fullName}", fontWeight = FontWeight.SemiBold, color = Emerald)
                chosen.description.takeIf { it.isNotBlank() }?.let {
                    Text(it, color = Muted, style = MaterialTheme.typography.bodySmall)
                }
                if (!chosen.canPush) {
                    Text("当前 GitHub 授权对该仓库没有写权限，将按只读项目保存。", color = MaterialTheme.colorScheme.error)
                }
                OutlinedTextField(projectName, { projectName = it }, label = { Text("项目名称") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(baseBranch, { baseBranch = it }, label = { Text("基础分支") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(memoryMb, { memoryMb = it.filter(Char::isDigit) }, label = { Text("单任务内存上限 MB") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(allowNetwork, { allowNetwork = it })
                    Text("允许构建任务联网下载依赖")
                }
                Button(
                    enabled = !saveBusy && !repositoryBusy && selectedConnectionId != null,
                    onClick = {
                        val connectionId = selectedConnectionId ?: return@Button
                        saveBusy = true
                        scope.launch {
                            when (
                                val result = AgentApi.saveProject(
                                    context = context,
                                    connectionId = connectionId,
                                    repository = chosen.fullName,
                                    name = projectName.trim().ifBlank { chosen.name },
                                    baseBranch = baseBranch.trim().ifBlank { chosen.defaultBranch },
                                    allowPush = chosen.canPush,
                                    allowPr = chosen.canPush,
                                    allowNetwork = allowNetwork,
                                    sandboxMemoryMb = memoryMb.toIntOrNull()?.coerceIn(128, 16384) ?: 2048,
                                )
                            ) {
                                is AgentApiResult.Failure -> snackbar.showSnackbar(result.message)
                                is AgentApiResult.Success -> {
                                    onProjectSaved(result.value)
                                    snackbar.showSnackbar("GitHub 项目已加入当前 FDEX 账号")
                                }
                            }
                            saveBusy = false
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (saveBusy) "保存中…" else "添加 Agent 项目") }
            }
        }
    }
}
