package com.b8vipvip.fdex.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import com.b8vipvip.fdex.data.CentralSessionStore
import com.b8vipvip.fdex.data.ClientPreferences
import com.b8vipvip.fdex.data.LegacyDataMigration
import com.b8vipvip.fdex.data.LegacyMemoryScopeRegistration
import com.b8vipvip.fdex.data.LocalAccountDataExport
import com.b8vipvip.fdex.network.CentralAuthApi
import com.b8vipvip.fdex.network.CentralAuthResult
import com.b8vipvip.fdex.network.CentralDeviceSessionDto
import com.b8vipvip.fdex.network.CentralMemoryStatusDto
import com.b8vipvip.fdex.network.CentralSecurityEventDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.time.LocalDate

@Composable
internal fun CenterSecurityScreen(
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
    onRequireLogin: () -> Unit,
) {
    val context = LocalContext.current
    val prefs = remember { ClientPreferences(context) }
    val sessionsStore = remember { CentralSessionStore(context) }
    val scope = rememberCoroutineScope()
    var autoArchive by remember { mutableStateOf(prefs.autoArchiveKnowledge()) }
    var remoteMemory by remember { mutableStateOf(prefs.remoteLongTermMemory()) }
    var currentPassword by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }
    var passwordBusy by remember { mutableStateOf(false) }
    var devices by remember { mutableStateOf<List<CentralDeviceSessionDto>>(emptyList()) }
    var securityEvents by remember { mutableStateOf<List<CentralSecurityEventDto>>(emptyList()) }
    var loadingSecurity by remember { mutableStateOf(false) }
    var memoryStatus by remember { mutableStateOf<CentralMemoryStatusDto?>(null) }
    var memoryLoading by remember { mutableStateOf(false) }
    var clearMemoryPassword by remember { mutableStateOf("") }
    var clearMemoryConfirmed by remember { mutableStateOf(false) }
    var clearMemoryBusy by remember { mutableStateOf(false) }
    var exportBusy by remember { mutableStateOf(false) }
    var pendingExport by remember { mutableStateOf("") }
    var migration by remember { mutableStateOf(LegacyDataMigration.status(context)) }
    var migrationBusy by remember { mutableStateOf(false) }
    var deletePassword by remember { mutableStateOf("") }
    var deleteConfirmed by remember { mutableStateOf(false) }
    var deleteBusy by remember { mutableStateOf(false) }

    val exportLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/json"),
    ) { uri ->
        val payload = pendingExport
        if (uri == null || payload.isBlank()) return@rememberLauncherForActivityResult
        scope.launch {
            val saved = withContext(Dispatchers.IO) {
                runCatching {
                    context.contentResolver.openOutputStream(uri)?.bufferedWriter()?.use { it.write(payload) }
                        ?: error("无法打开导出文件")
                }.isSuccess
            }
            snackbar.showSnackbar(if (saved) "FDEX 数据导出已保存" else "数据导出文件保存失败")
            pendingExport = ""
        }
    }

    suspend fun reloadSecurity() {
        loadingSecurity = true
        when (val result = CentralAuthApi.listSessions(context)) {
            is CentralAuthResult.Success -> devices = result.value
            is CentralAuthResult.Failure -> snackbar.showSnackbar(result.message)
        }
        when (val result = CentralAuthApi.securityEvents(context)) {
            is CentralAuthResult.Success -> securityEvents = result.value
            is CentralAuthResult.Failure -> Unit
        }
        loadingSecurity = false
    }

    suspend fun reloadMemoryStatus(showError: Boolean = false) {
        memoryLoading = true
        when (val result = CentralAuthApi.memoryStatus(context)) {
            is CentralAuthResult.Success -> memoryStatus = result.value
            is CentralAuthResult.Failure -> if (showError) snackbar.showSnackbar(result.message)
        }
        memoryLoading = false
    }

    LaunchedEffect(Unit) {
        // Phase 7.3 can retroactively attach this device's already-created opaque local
        // memory scopes to the authenticated user without exposing an owner id to Android.
        LegacyMemoryScopeRegistration.localScopeTokens(context).forEach { token ->
            CentralAuthApi.registerMemoryScope(context, token)
        }
        reloadSecurity()
        reloadMemoryStatus()
    }

    LazyColumn(
        Modifier.fillMaxSize().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("FDEX 中心账号安全", fontWeight = FontWeight.Bold)
                    Text(sessionsStore.email(), color = Emerald)
                    Text("User ID：${sessionsStore.userId()}", color = Muted, style = MaterialTheme.typography.bodySmall)
                    Text("密码、设备 Session、数据导出和删除操作都由中心账号统一管理。", color = Muted)
                }
            }
        }

        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Text("隐私与长期记忆", fontWeight = FontWeight.Bold)
                    CenterPreferenceToggle(
                        title = "自动整理聊天到本机知识库",
                        description = "关闭后不再自动回填或新增聊天知识；已有知识仍可手动查看和检索。",
                        checked = autoArchive,
                    ) {
                        autoArchive = it; prefs.setAutoArchiveKnowledge(it); onChanged()
                    }
                    CenterPreferenceToggle(
                        title = "启用 MemPalace / Letta 长期记忆",
                        description = "关闭后新对话不会进行跨会话远程召回或写入。远程 namespace 绑定中心 user_id。",
                        checked = remoteMemory,
                    ) {
                        remoteMemory = it; prefs.setRemoteLongTermMemory(it); onChanged()
                    }
                }
            }
        }

        item { SectionTitle("我的数据") }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(9.dp)) {
                    Text("数据导出与远程长期记忆", fontWeight = FontWeight.Bold)
                    val status = memoryStatus
                    when {
                        memoryLoading && status == null -> Text("正在读取长期记忆状态…", color = Muted)
                        status == null -> Text("长期记忆状态暂不可用", color = Muted)
                        else -> {
                            Text("已登记设备记忆空间：${status.registeredDeviceScopes}", color = Muted)
                            Text("最近清理状态：${memoryPhaseLabel(status.phase)}", color = if (status.lastError.isBlank()) Muted else MaterialTheme.colorScheme.error)
                            if (status.lastError.isNotBlank()) Text("失败代码：${status.lastError}", color = MaterialTheme.colorScheme.error)
                            if (status.busy) Text("当前数据操作：${status.operation.ifBlank { "处理中" }}", color = Blue)
                        }
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            enabled = !memoryLoading,
                            onClick = { scope.launch { reloadMemoryStatus(showError = true) } },
                        ) { Text("刷新状态") }
                        Button(
                            enabled = !exportBusy && memoryStatus?.busy != true,
                            onClick = {
                                exportBusy = true
                                scope.launch {
                                    when (val result = CentralAuthApi.exportData(context)) {
                                        is CentralAuthResult.Success -> {
                                            val payload = JSONObject()
                                                .put("schema_version", 1)
                                                .put("fdex_center", JSONObject(result.value))
                                                .put("android_local", LocalAccountDataExport.snapshot(context))
                                                .toString(2)
                                            pendingExport = payload
                                            exportLauncher.launch("fdex-data-export-${LocalDate.now()}.json")
                                        }
                                        is CentralAuthResult.Failure -> snackbar.showSnackbar(result.message)
                                    }
                                    exportBusy = false
                                }
                            },
                        ) { Text(if (exportBusy) "准备导出…" else "导出我的数据") }
                    }
                    Text("导出包含中心账号公开资料、设备记录、安全审计、GitHub/Coding Agent 元数据、远程长期记忆以及当前账号 Android 本机业务数据；不会导出密码、Token、API Key 或 GitHub 密钥。", color = Muted, style = MaterialTheme.typography.bodySmall)
                    OutlinedTextField(
                        clearMemoryPassword,
                        { clearMemoryPassword = it },
                        label = { Text("当前密码") },
                        visualTransformation = PasswordVisualTransformation(),
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = clearMemoryConfirmed, onCheckedChange = { clearMemoryConfirmed = it })
                        Text("我确认清空远程长期记忆")
                    }
                    Button(
                        enabled = !clearMemoryBusy && clearMemoryConfirmed && clearMemoryPassword.isNotBlank() && memoryStatus?.busy != true,
                        onClick = {
                            clearMemoryBusy = true
                            scope.launch {
                                when (val result = CentralAuthApi.clearMemory(context, clearMemoryPassword)) {
                                    is CentralAuthResult.Success -> {
                                        clearMemoryPassword = ""
                                        clearMemoryConfirmed = false
                                        snackbar.showSnackbar("远程长期记忆已清空；后续对话仍可重新形成新记忆")
                                        reloadMemoryStatus()
                                    }
                                    is CentralAuthResult.Failure -> {
                                        snackbar.showSnackbar(result.message)
                                        reloadMemoryStatus()
                                    }
                                }
                                clearMemoryBusy = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (clearMemoryBusy) "正在清空…" else if (memoryStatus?.phase == "failed") "重试清空长期记忆" else "清空远程长期记忆") }
                    Text("这里只删除服务器 MemPalace / Qdrant / Letta 记忆，不删除当前 Android 本机员工、聊天、项目和知识库。若继续启用长期记忆，之后的新对话可以重新产生记忆。", color = Muted, style = MaterialTheme.typography.bodySmall)
                }
            }
        }

        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("修改中心账号密码", fontWeight = FontWeight.Bold)
                    Text("修改成功后，其它设备 Session 会立即注销；当前设备保持登录。", color = Muted)
                    OutlinedTextField(currentPassword, { currentPassword = it }, label = { Text("当前密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(newPassword, { newPassword = it }, label = { Text("新密码（至少 8 位）") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), singleLine = true)
                    OutlinedTextField(confirmPassword, { confirmPassword = it }, label = { Text("再次输入新密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), singleLine = true)
                    Button(
                        enabled = !passwordBusy && currentPassword.isNotBlank() && newPassword.length >= 8 && newPassword == confirmPassword,
                        onClick = {
                            passwordBusy = true
                            scope.launch {
                                when (val result = CentralAuthApi.changePassword(context, currentPassword, newPassword)) {
                                    is CentralAuthResult.Success -> {
                                        currentPassword = ""; newPassword = ""; confirmPassword = ""
                                        snackbar.showSnackbar("中心账号密码已修改，其它设备已注销")
                                        reloadSecurity()
                                    }
                                    is CentralAuthResult.Failure -> snackbar.showSnackbar(result.message)
                                }
                                passwordBusy = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (passwordBusy) "修改中…" else "修改密码") }
                }
            }
        }

        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                SectionTitle("登录设备")
                OutlinedButton(enabled = !loadingSecurity, onClick = { scope.launch { reloadSecurity() } }) { Text("刷新") }
            }
        }
        if (devices.isEmpty()) {
            item { Card(Modifier.fillMaxWidth()) { Text(if (loadingSecurity) "正在读取设备…" else "没有可显示的 Session", modifier = Modifier.padding(14.dp), color = Muted) } }
        } else {
            items(devices, key = { "session-${it.id}" }) { device ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${if (device.current) "当前设备 · " else ""}${device.deviceName.ifBlank { "未知设备" }}", fontWeight = FontWeight.Bold)
                        Text("IP：${device.clientIp.ifBlank { "未记录" }}", color = Muted)
                        Text("最近活动：${device.lastSeenAt.ifBlank { device.createdAt }}", color = Muted, style = MaterialTheme.typography.bodySmall)
                        Text("状态：${if (device.active) "在线/可刷新" else "已注销或已过期"}", color = if (device.active) Emerald else Muted)
                        if (device.active) {
                            OutlinedButton(onClick = {
                                scope.launch {
                                    when (val result = CentralAuthApi.revokeSession(context, device.id)) {
                                        is CentralAuthResult.Success -> {
                                            if (device.current) {
                                                sessionsStore.clear(); AgentEmployeePreferences(context).clearAccountCredential(); onRequireLogin()
                                            } else {
                                                snackbar.showSnackbar("该设备已注销"); reloadSecurity()
                                            }
                                        }
                                        is CentralAuthResult.Failure -> snackbar.showSnackbar(result.message)
                                    }
                                }
                            }) { Text(if (device.current) "注销当前设备" else "注销此设备") }
                        }
                    }
                }
            }
        }
        item {
            OutlinedButton(
                onClick = {
                    scope.launch {
                        when (val result = CentralAuthApi.logoutAll(context)) {
                            is CentralAuthResult.Success -> {
                                sessionsStore.clear(); AgentEmployeePreferences(context).clearAccountCredential(); onRequireLogin()
                            }
                            is CentralAuthResult.Failure -> snackbar.showSnackbar(result.message)
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("注销全部设备") }
        }

        item { SectionTitle("登录安全审计") }
        if (securityEvents.isEmpty()) {
            item { Card(Modifier.fillMaxWidth()) { Text("暂时没有安全事件", modifier = Modifier.padding(14.dp), color = Muted) } }
        } else {
            items(securityEvents.take(12)) { event ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text(securityEventLabel(event.event), fontWeight = FontWeight.SemiBold)
                        val risk = securityRiskLabel(event.risk)
                        if (risk.isNotBlank()) Text("风险提示：$risk", color = MaterialTheme.colorScheme.error)
                        Text("${event.deviceName.ifBlank { "未知设备" }} · ${event.clientIp.ifBlank { "IP 未记录" }}", color = Muted)
                        Text(event.createdAt, color = Muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }

        item { SectionTitle("旧版本机数据迁移") }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("旧数据库 fdex-local-v3.db", fontWeight = FontWeight.Bold)
                    Text("旧数据 ${migration.legacyRecords} 条 · 当前账号 ${migration.currentRecords} 条", color = Muted)
                    Text(migration.message, color = Muted)
                    Button(
                        enabled = migration.eligible && !migrationBusy,
                        onClick = {
                            migrationBusy = true
                            scope.launch {
                                val result = LegacyDataMigration.migrateToCurrentAccount(context)
                                if (result.isSuccess) {
                                    snackbar.showSnackbar("已迁移 ${result.getOrDefault(0)} 条旧版本机数据；旧数据库仍保留")
                                    migration = LegacyDataMigration.status(context); onChanged()
                                } else {
                                    snackbar.showSnackbar(result.exceptionOrNull()?.message ?: "旧数据迁移失败")
                                }
                                migrationBusy = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (migrationBusy) "迁移中…" else "迁移旧数据到当前账号") }
                }
            }
        }

        item { SectionTitle("注销 FDEX 账号") }
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("永久注销中心账号", color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                    Text("会先锁定当前账号的数据删除操作并清除已登记的 MemPalace / Qdrant / Letta 长期记忆，再删除 GitHub 连接、Coding Agent 项目、账号沙箱、中心账号和全部 Session。成功后当前账号 Android 本机数据库也会删除；旧版 fdex-local-v3.db 备份不会自动删除。", color = Muted)
                    OutlinedTextField(deletePassword, { deletePassword = it }, label = { Text("输入当前密码确认") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), singleLine = true)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = deleteConfirmed, onCheckedChange = { deleteConfirmed = it })
                        Text("我确认永久注销当前 FDEX 账号")
                    }
                    Button(
                        enabled = !deleteBusy && deleteConfirmed && deletePassword.isNotBlank() && memoryStatus?.busy != true,
                        onClick = {
                            deleteBusy = true
                            scope.launch {
                                when (val result = CentralAuthApi.deleteAccount(context, deletePassword)) {
                                    is CentralAuthResult.Success -> {
                                        LegacyDataMigration.deleteCurrentAccountDatabase(context)
                                        sessionsStore.clear(); AgentEmployeePreferences(context).clearAccountCredential()
                                        onRequireLogin()
                                    }
                                    is CentralAuthResult.Failure -> {
                                        snackbar.showSnackbar(result.message)
                                        reloadMemoryStatus()
                                    }
                                }
                                deleteBusy = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (deleteBusy) "正在注销…" else "永久注销账号") }
                }
            }
        }
    }
}

@Composable
private fun CenterPreferenceToggle(
    title: String,
    description: String,
    checked: Boolean,
    onChange: (Boolean) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(description, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

private fun memoryPhaseLabel(phase: String): String = when (phase) {
    "idle" -> "尚未执行清理"
    "started" -> "正在开始"
    "letta_erased" -> "Letta 已清除"
    "qdrant_erased" -> "向量记忆已清除"
    "completed" -> "已完成"
    "failed" -> "上次清理失败，可重试"
    else -> phase
}

private fun securityEventLabel(event: String): String = when (event) {
    "register" -> "账号注册"
    "login_success" -> "登录成功"
    "login_failed" -> "登录失败"
    "login_rate_limited" -> "登录被限流"
    "password_changed" -> "密码已修改"
    "password_reset_requested" -> "请求密码重置"
    "password_reset_code_failed" -> "密码重置验证码失败"
    "password_reset_completed" -> "密码重置完成"
    "session_revoked" -> "设备 Session 已注销"
    else -> event
}

private fun securityRiskLabel(risk: String): String = risk.split(',').mapNotNull {
    when (it.trim()) {
        "new_device" -> "新设备"
        "new_ip" -> "新 IP"
        "repeated_failures" -> "连续密码错误"
        "rate_limited" -> "登录失败次数过多"
        "invalid_code" -> "验证码错误"
        else -> null
    }
}.distinct().joinToString("、")
