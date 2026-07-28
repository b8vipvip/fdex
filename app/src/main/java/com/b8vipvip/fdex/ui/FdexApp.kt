package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
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
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.network.ServerApi
import com.b8vipvip.fdex.network.ServerCheckResult
import com.b8vipvip.fdex.update.ApkUpdater
import com.b8vipvip.fdex.update.GitHubUpdateService
import com.b8vipvip.fdex.update.ReleaseInfo
import com.b8vipvip.fdex.update.UpdateCheckResult
import com.b8vipvip.fdex.update.UpdatePreferences
import kotlinx.coroutines.launch

private enum class Screen { HOME, ABOUT }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FdexApp() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var screen by remember { mutableStateOf(Screen.HOME) }
    var isChecking by remember { mutableStateOf(false) }
    var isCheckingServer by remember { mutableStateOf(false) }
    var serverStatus by remember { mutableStateOf("未检测") }
    var availableRelease by remember { mutableStateOf<ReleaseInfo?>(null) }

    suspend fun checkUpdate(manual: Boolean) {
        if (isChecking) return
        isChecking = true
        val result = GitHubUpdateService.checkForUpdate(BuildConfig.VERSION_NAME)
        UpdatePreferences.recordCheck(context)
        when (result) {
            is UpdateCheckResult.UpdateAvailable -> availableRelease = result.release
            UpdateCheckResult.UpToDate -> if (manual) snackbar.showSnackbar("当前已是最新版本")
            is UpdateCheckResult.Failed -> if (manual) snackbar.showSnackbar(result.message)
        }
        isChecking = false
    }

    suspend fun checkServer() {
        if (isCheckingServer) return
        isCheckingServer = true
        serverStatus = when (val result = ServerApi.checkHealth()) {
            is ServerCheckResult.Online -> "在线 · ${result.service} ${result.version}"
            is ServerCheckResult.Offline -> "连接失败 · ${result.message}"
        }
        isCheckingServer = false
    }

    LaunchedEffect(Unit) {
        if (UpdatePreferences.shouldCheckOnLaunch(context)) {
            checkUpdate(manual = false)
        }
        checkServer()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (screen == Screen.HOME) "FDEX" else "设置 · 关于") },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar(modifier = Modifier.navigationBarsPadding()) {
                NavigationBarItem(
                    selected = screen == Screen.HOME,
                    onClick = { screen = Screen.HOME },
                    icon = { Icon(Icons.Outlined.Home, contentDescription = null) },
                    label = { Text("首页") },
                )
                NavigationBarItem(
                    selected = screen == Screen.ABOUT,
                    onClick = { screen = Screen.ABOUT },
                    icon = { Icon(Icons.Outlined.Info, contentDescription = null) },
                    label = { Text("设置") },
                )
            }
        },
    ) { padding ->
        when (screen) {
            Screen.HOME -> HomeScreen(Modifier.padding(padding), serverStatus)
            Screen.ABOUT -> AboutScreen(
                modifier = Modifier.padding(padding),
                isChecking = isChecking,
                isCheckingServer = isCheckingServer,
                serverStatus = serverStatus,
                onCheckUpdate = { scope.launch { checkUpdate(manual = true) } },
                onCheckServer = { scope.launch { checkServer() } },
            )
        }
    }

    availableRelease?.let { release ->
        UpdateDialog(
            release = release,
            onDismiss = { availableRelease = null },
            onUpdate = {
                ApkUpdater.downloadAndInstall(context, release)
                availableRelease = null
            },
            onOpenRelease = { ApkUpdater.openReleasePage(context, release.htmlUrl) },
        )
    }
}

@Composable
private fun HomeScreen(modifier: Modifier = Modifier, serverStatus: String) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "Android 客户端已就绪",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )
        Text(
            text = "客户端只访问 FDEX 服务端，第三方 AI 接口地址、模型和密钥统一在服务器配置。",
            style = MaterialTheme.typography.bodyLarge,
        )
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("服务端", fontWeight = FontWeight.SemiBold)
                Text(BuildConfig.SERVER_BASE_URL)
                Text(serverStatus, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("版本更新", fontWeight = FontWeight.SemiBold)
                Text("App 启动时会自动检查 GitHub Release，也可以在“关于”页面手动检查。")
            }
        }
    }
}

@Composable
private fun AboutScreen(
    modifier: Modifier = Modifier,
    isChecking: Boolean,
    isCheckingServer: Boolean,
    serverStatus: String,
    onCheckUpdate: () -> Unit,
    onCheckServer: () -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(18.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text("FDEX", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                HorizontalDivider()
                InfoRow("版本名称", BuildConfig.VERSION_NAME)
                InfoRow("版本号", BuildConfig.VERSION_CODE.toString())
                InfoRow("构建提交", BuildConfig.GIT_SHA)
                InfoRow("服务端", BuildConfig.SERVER_BASE_URL)
                InfoRow("服务状态", serverStatus)
                InfoRow("更新来源", "GitHub Releases")
            }
        }

        Button(
            onClick = onCheckServer,
            enabled = !isCheckingServer,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (isCheckingServer) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(end = 10.dp),
                    strokeWidth = 2.dp,
                )
                Text("正在连接")
            } else {
                Text("检测服务端")
            }
        }

        Button(
            onClick = onCheckUpdate,
            enabled = !isChecking,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (isChecking) {
                CircularProgressIndicator(
                    modifier = Modifier.padding(end = 10.dp),
                    strokeWidth = 2.dp,
                )
                Text("正在检查")
            } else {
                Text("检查更新")
            }
        }

        Text(
            text = "新版本必须由 GitHub Release 提供 APK。第三方 API Key 仅保存在服务端 .env 中，不会包含在 APK 内。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.padding(horizontal = 8.dp))
        Text(value, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun UpdateDialog(
    release: ReleaseInfo,
    onDismiss: () -> Unit,
    onUpdate: () -> Unit,
    onOpenRelease: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("发现新版本 ${release.normalizedVersion}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (release.name.isNotBlank()) {
                    Text(release.name, fontWeight = FontWeight.SemiBold)
                }
                Text(
                    text = release.body.ifBlank { "GitHub 已发布新版本。" }.take(800),
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (release.apkUrl == null) {
                    Text(
                        "该 Release 没有 APK，将打开 GitHub 发布页面。",
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
        confirmButton = {
            Button(onClick = if (release.apkUrl != null) onUpdate else onOpenRelease) {
                Text(if (release.apkUrl != null) "立即更新" else "打开发布页")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("稍后") }
        },
    )
}
