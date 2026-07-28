package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.network.ServerApi
import com.b8vipvip.fdex.network.ServerCheckResult
import com.b8vipvip.fdex.update.ApkUpdater
import com.b8vipvip.fdex.update.ReleaseInfo
import com.b8vipvip.fdex.update.ServerUpdateService
import com.b8vipvip.fdex.update.UpdateCheckResult
import com.b8vipvip.fdex.update.UpdatePreferences
import kotlinx.coroutines.launch

internal sealed interface Route {
    data object Login : Route
    data object Register : Route
    data object Messages : Route
    data object Work : Route
    data object Discover : Route
    data object Me : Route
    data class EmployeeChat(val id: Long) : Route
    data object Employees : Route
    data object AddEmployee : Route
    data object NewProject : Route
    data class ProjectDetail(val id: Long) : Route
    data object NewGroup : Route
    data class GroupChat(val id: Long) : Route
    data object Account : Route
    data object Settings : Route
    data object Deleted : Route
    data object About : Route
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FdexApp() {
    val context = LocalContext.current
    val repo = remember { AppRepository(context) }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var revision by remember { mutableIntStateOf(0) }
    var route by remember { mutableStateOf<Route>(if (repo.isLoggedIn()) Route.Messages else Route.Login) }
    val history = remember { mutableStateListOf<Route>() }
    var availableRelease by remember { mutableStateOf<ReleaseInfo?>(null) }
    var updateChecking by remember { mutableStateOf(false) }
    var serverStatus by remember { mutableStateOf("检测中…") }

    fun touch() { revision++ }
    fun go(next: Route, keepCurrent: Boolean = true) {
        if (keepCurrent && route != next) history.add(route)
        route = next
    }
    fun back() {
        route = if (history.isNotEmpty()) history.removeAt(history.lastIndex) else Route.Messages
    }
    suspend fun checkUpdate(manual: Boolean) {
        if (updateChecking) return
        updateChecking = true
        when (val result = ServerUpdateService.checkForUpdate(BuildConfig.VERSION_NAME)) {
            is UpdateCheckResult.UpdateAvailable -> availableRelease = result.release
            UpdateCheckResult.UpToDate -> if (manual) snackbar.showSnackbar("当前已是最新版本")
            is UpdateCheckResult.Failed -> if (manual) snackbar.showSnackbar(result.message)
        }
        UpdatePreferences.recordCheck(context)
        updateChecking = false
    }

    LaunchedEffect(Unit) {
        serverStatus = when (val result = ServerApi.checkHealth()) {
            is ServerCheckResult.Online -> "在线 · ${result.version}"
            is ServerCheckResult.Offline -> "连接失败 · ${result.message}"
        }
        if (UpdatePreferences.shouldCheckOnLaunch(context)) checkUpdate(false)
    }

    val mainTab = route == Route.Messages || route == Route.Work || route == Route.Discover || route == Route.Me
    val title = when (val current = route) {
        Route.Login -> "登录"
        Route.Register -> "注册"
        Route.Messages -> "消息"
        Route.Work -> "工作"
        Route.Discover -> "发现"
        Route.Me -> "我的"
        is Route.EmployeeChat -> repo.employee(current.id)?.name ?: "聊天"
        Route.Employees -> "AI 员工管理"
        Route.AddEmployee -> "添加员工"
        Route.NewProject -> "新增工作"
        is Route.ProjectDetail -> repo.project(current.id)?.title ?: "工作详情"
        Route.NewGroup -> "创建工作群"
        is Route.GroupChat -> repo.group(current.id)?.name ?: "工作群"
        Route.Account -> "账号信息"
        Route.Settings -> "设置"
        Route.Deleted -> "最近删除"
        Route.About -> "关于 FDEX"
    }

    Scaffold(
        containerColor = PageBg,
        topBar = {
            if (route != Route.Login && route != Route.Register) {
                TopAppBar(
                    title = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    navigationIcon = {
                        if (!mainTab) TextButton(onClick = { back() }) { Text("‹", fontSize = 30.sp) }
                    },
                    actions = {
                        when (route) {
                            Route.Messages -> TextButton(onClick = { go(Route.NewGroup) }) { Text("＋", fontSize = 26.sp) }
                            Route.Work -> TextButton(onClick = { go(Route.NewProject) }) { Text("＋", fontSize = 26.sp) }
                            else -> Unit
                        }
                    },
                )
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            if (mainTab) {
                NavigationBar(modifier = Modifier.navigationBarsPadding()) {
                    listOf(
                        Triple<Route, String, String>(Route.Messages, "💬", "消息"),
                        Triple<Route, String, String>(Route.Work, "📁", "工作"),
                        Triple<Route, String, String>(Route.Discover, "🧭", "发现"),
                        Triple<Route, String, String>(Route.Me, "👤", "我的"),
                    ).forEach { (target, icon, label) ->
                        NavigationBarItem(
                            selected = route == target,
                            onClick = { history.clear(); route = target },
                            icon = { Text(icon, fontSize = 20.sp) },
                            label = { Text(label) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (val current = route) {
                Route.Login -> LoginScreen(repo, onLogin = { touch(); history.clear(); route = Route.Messages }, onRegister = { route = Route.Register })
                Route.Register -> RegisterScreen(repo, onDone = { touch(); history.clear(); route = Route.Messages }, onLogin = { route = Route.Login })
                Route.Messages -> MessagesScreen(repo, revision, onEmployee = { go(Route.EmployeeChat(it)) }, onGroup = { go(Route.GroupChat(it)) }, onAddEmployee = { go(Route.AddEmployee) })
                Route.Work -> WorkScreen(repo, revision, onOpen = { go(Route.ProjectDetail(it)) }, onNew = { go(Route.NewProject) })
                Route.Discover -> DiscoverScreen()
                Route.Me -> MeScreen(
                    repo,
                    revision,
                    onAccount = { go(Route.Account) },
                    onEmployees = { go(Route.Employees) },
                    onDeleted = { go(Route.Deleted) },
                    onSettings = { go(Route.Settings) },
                    onAbout = { go(Route.About) },
                    onLogout = { repo.logout(); history.clear(); route = Route.Login; touch() },
                )
                is Route.EmployeeChat -> EmployeeChatScreen(repo, current.id, revision, onChanged = { touch() }, onOpenManage = { go(Route.Employees) }, snackbar = snackbar)
                Route.Employees -> EmployeeManageScreen(repo, revision, onAdd = { go(Route.AddEmployee) }, onChat = { go(Route.EmployeeChat(it)) }, onChanged = { touch() })
                Route.AddEmployee -> AddEmployeeScreen(repo) { touch(); back() }
                Route.NewProject -> NewProjectScreen(repo) { id -> touch(); go(Route.ProjectDetail(id), keepCurrent = false) }
                is Route.ProjectDetail -> ProjectDetailScreen(repo, current.id, revision, onChanged = { touch() }, onGroup = { go(Route.GroupChat(it)) }, snackbar = snackbar)
                Route.NewGroup -> NewGroupScreen(repo) { id -> touch(); go(Route.GroupChat(id), keepCurrent = false) }
                is Route.GroupChat -> GroupChatScreen(repo, current.id, revision, onChanged = { touch() }, snackbar = snackbar)
                Route.Account -> AccountScreen(repo, onChanged = { touch() }, snackbar = snackbar)
                Route.Settings -> SettingsScreen(repo, revision, onAbout = { go(Route.About) }, onChanged = { touch() })
                Route.Deleted -> DeletedScreen(repo, revision, onChanged = { touch() })
                Route.About -> AboutScreen(serverStatus, updateChecking) { scope.launch { checkUpdate(true) } }
            }
        }
    }

    availableRelease?.let { release ->
        AlertDialog(
            onDismissRequest = { availableRelease = null },
            title = { Text("发现新版本 ${release.normalizedVersion}") },
            text = { Text(release.body.ifBlank { "FDEX 服务端已同步新版本。" }.take(800)) },
            confirmButton = {
                Button(onClick = {
                    ApkUpdater.downloadAndInstall(context, release)
                    availableRelease = null
                }) { Text("立即更新") }
            },
            dismissButton = { TextButton(onClick = { availableRelease = null }) { Text("稍后") } },
        )
    }
}
