package com.b8vipvip.fdex.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.GroupMessage
import com.b8vipvip.fdex.data.Profile
import com.b8vipvip.fdex.data.Project
import com.b8vipvip.fdex.data.ProjectAsset
import com.b8vipvip.fdex.data.Report
import com.b8vipvip.fdex.data.WorkGroup
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.ClientAiApi
import com.b8vipvip.fdex.network.ServerApi
import com.b8vipvip.fdex.network.ServerCheckResult
import com.b8vipvip.fdex.update.ApkUpdater
import com.b8vipvip.fdex.update.GitHubUpdateService
import com.b8vipvip.fdex.update.ReleaseInfo
import com.b8vipvip.fdex.update.UpdateCheckResult
import com.b8vipvip.fdex.update.UpdatePreferences
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val PageBg = Color(0xFFF1F5F9)
private val Emerald = Color(0xFF059669)
private val Blue = Color(0xFF2563EB)
private val Muted = Color(0xFF64748B)

private sealed interface Route {
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
    fun go(next: Route, rememberCurrent: Boolean = true) {
        if (rememberCurrent && route != next) history.add(route)
        route = next
    }
    fun back() {
        route = if (history.isNotEmpty()) history.removeAt(history.lastIndex) else Route.Messages
    }
    suspend fun checkUpdate(manual: Boolean) {
        if (updateChecking) return
        updateChecking = true
        val result = GitHubUpdateService.checkForUpdate(BuildConfig.VERSION_NAME)
        UpdatePreferences.recordCheck(context)
        when (result) {
            is UpdateCheckResult.UpdateAvailable -> availableRelease = result.release
            UpdateCheckResult.UpToDate -> if (manual) snackbar.showSnackbar("当前已是最新版本")
            is UpdateCheckResult.Failed -> if (manual) snackbar.showSnackbar(result.message)
        }
        updateChecking = false
    }

    LaunchedEffect(Unit) {
        serverStatus = when (val result = ServerApi.checkHealth()) {
            is ServerCheckResult.Online -> "在线 · ${result.version}"
            is ServerCheckResult.Offline -> "连接失败 · ${result.message}"
        }
        if (UpdatePreferences.shouldCheckOnLaunch(context)) checkUpdate(false)
    }

    val mainTab = route in listOf(Route.Messages, Route.Work, Route.Discover, Route.Me)
    val title = when (val r = route) {
        Route.Messages -> "消息"
        Route.Work -> "工作"
        Route.Discover -> "发现"
        Route.Me -> "我的"
        Route.Login -> "登录"
        Route.Register -> "注册"
        is Route.EmployeeChat -> repo.employee(r.id)?.name ?: "聊天"
        Route.Employees -> "AI 员工管理"
        Route.AddEmployee -> "添加员工"
        Route.NewProject -> "新增工作"
        is Route.ProjectDetail -> repo.project(r.id)?.title ?: "工作详情"
        Route.NewGroup -> "创建工作群"
        is Route.GroupChat -> repo.group(r.id)?.name ?: "工作群"
        Route.Account -> "账号信息"
        Route.Settings -> "设置"
        Route.Deleted -> "最近删除"
        Route.About -> "关于 FDEX"
    }

    Scaffold(
        containerColor = PageBg,
        topBar = {
            if (route !in listOf(Route.Login, Route.Register)) {
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
                        Triple(Route.Messages as Route, "💬", "消息"),
                        Triple(Route.Work as Route, "📁", "工作"),
                        Triple(Route.Discover as Route, "🧭", "发现"),
                        Triple(Route.Me as Route, "👤", "我的"),
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
            when (val r = route) {
                Route.Login -> LoginScreen(
                    repo = repo,
                    onLogin = { touch(); history.clear(); route = Route.Messages },
                    onRegister = { route = Route.Register },
                )
                Route.Register -> RegisterScreen(
                    repo = repo,
                    onDone = { touch(); history.clear(); route = Route.Messages },
                    onLogin = { route = Route.Login },
                )
                Route.Messages -> MessagesScreen(repo, revision, onEmployee = { go(Route.EmployeeChat(it)) }, onGroup = { go(Route.GroupChat(it)) }, onAddEmployee = { go(Route.AddEmployee) })
                Route.Work -> WorkScreen(repo, revision, onOpen = { go(Route.ProjectDetail(it)) }, onNew = { go(Route.NewProject) })
                Route.Discover -> DiscoverScreen()
                Route.Me -> MeScreen(repo, revision, onRoute = { go(it) }, onLogout = { repo.logout(); history.clear(); route = Route.Login; touch() })
                is Route.EmployeeChat -> EmployeeChatScreen(repo, r.id, revision, onChanged = { touch() }, onOpenProfile = { go(Route.Employees) }, snackbar = snackbar)
                Route.Employees -> EmployeeManageScreen(repo, revision, onAdd = { go(Route.AddEmployee) }, onChat = { go(Route.EmployeeChat(it)) }, onChanged = { touch() })
                Route.AddEmployee -> AddEmployeeScreen(repo, onDone = { touch(); back() }, snackbar = snackbar)
                Route.NewProject -> NewProjectScreen(repo, onDone = { id -> touch(); go(Route.ProjectDetail(id), rememberCurrent = false) })
                is Route.ProjectDetail -> ProjectDetailScreen(repo, r.id, revision, onChanged = { touch() }, onGroup = { go(Route.GroupChat(it)) }, snackbar = snackbar)
                Route.NewGroup -> NewGroupScreen(repo, onDone = { id -> touch(); go(Route.GroupChat(id), rememberCurrent = false) })
                is Route.GroupChat -> GroupChatScreen(repo, r.id, revision, onChanged = { touch() }, snackbar = snackbar)
                Route.Account -> AccountScreen(repo, onChanged = { touch() }, snackbar = snackbar)
                Route.Settings -> SettingsScreen(repo, revision, onAbout = { go(Route.About) }, onChanged = { touch() })
                Route.Deleted -> DeletedScreen(repo, revision, onChanged = { touch() })
                Route.About -> AboutScreen(
                    serverStatus = serverStatus,
                    updateChecking = updateChecking,
                    onCheckUpdate = { scope.launch { checkUpdate(true) } },
                )
            }
        }
    }

    availableRelease?.let { release ->
        AlertDialog(
            onDismissRequest = { availableRelease = null },
            title = { Text("发现新版本 ${release.normalizedVersion}") },
            text = { Text(release.body.ifBlank { "GitHub 已发布新版本。" }.take(800)) },
            confirmButton = {
                Button(onClick = {
                    if (release.apkUrl != null) ApkUpdater.downloadAndInstall(context, release)
                    else ApkUpdater.openReleasePage(context, release.htmlUrl)
                    availableRelease = null
                }) { Text(if (release.apkUrl != null) "立即更新" else "打开发布页") }
            },
            dismissButton = { TextButton(onClick = { availableRelease = null }) { Text("稍后") } },
        )
    }
}

@Composable
private fun LoginScreen(repo: AppRepository, onLogin: () -> Unit, onRegister: () -> Unit) {
    var email by remember { mutableStateOf("") }; var password by remember { mutableStateOf("") }; var error by remember { mutableStateOf("") }
    AuthFrame("登录 FDEX", "进入你的 AI 虚拟公司") {
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text("密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(onClick = { repo.login(email, password).onSuccess { onLogin() }.onFailure { error = it.message.orEmpty() } }, modifier = Modifier.fillMaxWidth()) { Text("登录") }
        TextButton(onClick = onRegister, modifier = Modifier.fillMaxWidth()) { Text(if (repo.hasAccount()) "重新创建本机账号" else "还没有账号？注册") }
        Text("账号数据保存在本机；AI 请求只发送到你的 FDEX 服务端。", style = MaterialTheme.typography.bodySmall, color = Muted)
    }
}

@Composable
private fun RegisterScreen(repo: AppRepository, onDone: () -> Unit, onLogin: () -> Unit) {
    var name by remember { mutableStateOf("") }; var email by remember { mutableStateOf("") }; var company by remember { mutableStateOf("") }; var password by remember { mutableStateOf("") }; var error by remember { mutableStateOf("") }
    AuthFrame("创建 AI 虚拟公司", "注册后会自动配备资料管理员、业务策划、行业研究员和执行经理") {
        OutlinedTextField(name, { name = it }, label = { Text("你的名字") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(company, { company = it }, label = { Text("公司名称（可选）") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text("密码（至少 8 位）") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(onClick = { repo.register(name, email, password, company).onSuccess { onDone() }.onFailure { error = it.message.orEmpty() } }, modifier = Modifier.fillMaxWidth()) { Text("创建并进入") }
        TextButton(onClick = onLogin, modifier = Modifier.fillMaxWidth()) { Text("返回登录") }
    }
}

@Composable
private fun AuthFrame(title: String, subtitle: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().background(PageBg).verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.Center) {
        Text("FDEX", color = Emerald, fontWeight = FontWeight.Bold)
        Text(title, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
        Text(subtitle, color = Muted, modifier = Modifier.padding(top = 6.dp, bottom = 24.dp))
        Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp), content = content) }
    }
}

@Composable
private fun MessagesScreen(repo: AppRepository, revision: Int, onEmployee: (Long) -> Unit, onGroup: (Long) -> Unit, onAddEmployee: () -> Unit) {
    var query by remember { mutableStateOf("") }
    val employees = repo.employees().filter { query.isBlank() || "${it.name}${it.position}${it.department}".contains(query, true) }
    val groups = repo.groups().filter { query.isBlank() || "${it.name}${it.description}".contains(query, true) }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { OutlinedTextField(query, { query = it }, label = { Text("搜索员工、群名或工作") }, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp), singleLine = true) }
        if (groups.isNotEmpty()) item { SectionTitle("工作群") }
        items(groups, key = { "g${it.id}" }) { g -> ConversationRow("👥", g.name, "${g.memberIds.size} 名成员", repo.groupMessages(g.id).lastOrNull()?.content ?: "工作群已创建") { onGroup(g.id) } }
        if (employees.isNotEmpty()) item { SectionTitle("AI 员工") }
        items(employees, key = { "e${it.id}" }) { e -> ConversationRow(employeeEmoji(e), e.name, e.position, repo.messages(e.id).lastOrNull()?.content ?: "开始与 AI 员工沟通") { onEmployee(e.id) } }
        item { OutlinedButton(onClick = onAddEmployee, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) { Text("＋ 添加 AI 员工") } }
    }
}

@Composable
private fun ConversationRow(icon: String, title: String, sub: String, preview: String, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(icon)
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(sub, color = Emerald, fontSize = 12.sp)
                Text(preview, color = Muted, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 13.sp)
            }
            Text("›", color = Muted, fontSize = 24.sp)
        }
    }
}

@Composable
private fun EmployeeChatScreen(repo: AppRepository, employeeId: Long, revision: Int, onChanged: () -> Unit, onOpenProfile: () -> Unit, snackbar: SnackbarHostState) {
    val employee = repo.employee(employeeId) ?: return
    val scope = rememberCoroutineScope(); var text by remember { mutableStateOf("") }; var busy by remember { mutableStateOf(false) }; var menu by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(employeeEmoji(employee)); Column(Modifier.weight(1f).padding(start = 10.dp)) { Text("${employee.name} · ${employee.position}", fontWeight = FontWeight.SemiBold); Text(employee.department, color = Muted, fontSize = 12.sp) }
            Box { TextButton(onClick = { menu = true }) { Text("•••") }; DropdownMenu(menu, { menu = false }) { DropdownMenuItem({ Text("员工管理") }, { menu = false; onOpenProfile() }); DropdownMenuItem({ Text("清空聊天记录", color = MaterialTheme.colorScheme.error) }, { repo.clearMessages(employeeId); menu = false; onChanged() }) } }
        }
        Divider()
        LazyColumn(Modifier.weight(1f).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(repo.messages(employeeId), key = { it.id }) { m -> MessageBubble(m, employee) }
            if (busy) item { Text("${employee.name} 正在思考…", color = Muted, modifier = Modifier.padding(8.dp)) }
        }
        Row(Modifier.fillMaxWidth().background(Color.White).padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(text, { text = it }, placeholder = { Text("给员工安排任务…") }, modifier = Modifier.weight(1f), maxLines = 4)
            Spacer(Modifier.width(8.dp)); Button(enabled = text.isNotBlank() && !busy, onClick = {
                val prompt = text.trim(); text = ""; repo.addMessage(employeeId, "user", prompt); onChanged(); busy = true
                scope.launch {
                    val system = "你是 FDEX AI 虚拟公司的员工：${employee.name}，职位：${employee.position}，部门：${employee.department}。${employee.rolePrompt}。像真实同事一样简洁、主动、可执行地回答。"
                    when (val result = ClientAiApi.ask(system, prompt)) {
                        is AiGatewayResult.Success -> repo.addMessage(employeeId, "employee", result.content)
                        is AiGatewayResult.Failure -> { repo.addMessage(employeeId, "employee", "暂时无法完成请求：${result.message}"); snackbar.showSnackbar(result.message) }
                    }
                    busy = false; onChanged()
                }
            }) { Text("发送") }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage, employee: Employee) {
    val user = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) {
        if (!user) { Avatar(employeeEmoji(employee), 36); Spacer(Modifier.width(8.dp)) }
        Column(horizontalAlignment = if (user) Alignment.End else Alignment.Start, modifier = Modifier.fillMaxWidth(.78f)) {
            Text(if (user) "我" else employee.name, fontSize = 11.sp, color = Muted)
            Card(colors = CardDefaults.cardColors(containerColor = if (user) Color(0xFF10B981) else Color.White), shape = RoundedCornerShape(16.dp)) {
                Text(message.content, modifier = Modifier.padding(12.dp), color = if (user) Color.White else MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

@Composable
private fun WorkScreen(repo: AppRepository, revision: Int, onOpen: (Long) -> Unit, onNew: () -> Unit) {
    val projects = repo.projects()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Text("工作区", color = Blue, fontWeight = FontWeight.SemiBold)
            Text("让 AI 帮你分析资料并生成可执行方案", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Text("把业务问题、任务、流程或想法持续沉淀成一项工作。", color = Muted, modifier = Modifier.padding(top = 4.dp, bottom = 8.dp))
        }
        if (projects.isEmpty()) item { EmptyCard("🚀", "你还没有工作", "先创建一个工作，告诉 AI 你想解决什么业务问题。", "创建第一个工作", onNew) }
        items(projects, key = { it.id }) { p -> ProjectCard(p) { onOpen(p.id) } }
    }
}

@Composable
private fun ProjectCard(project: Project, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) { Text(project.title, fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.weight(1f)); StatusPill(project.status) }
            Text(project.description.ifBlank { "暂无工作描述" }, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 8.dp))
            Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.SpaceBetween) { Text("完整度 ${project.requirementScore}%", fontSize = 12.sp, color = Muted); Text(storageLabel(project.storageMode), fontSize = 12.sp, color = Muted) }
        }
    }
}

@Composable
private fun NewProjectScreen(repo: AppRepository, onDone: (Long) -> Unit) {
    var title by remember { mutableStateOf("") }; var description by remember { mutableStateOf("") }; var level by remember { mutableStateOf("business") }; var storage by remember { mutableStateOf("hybrid") }; var retention by remember { mutableStateOf("keep_forever") }; var allowAi by remember { mutableStateOf(true) }; var desensitize by remember { mutableStateOf(true) }; var auto by remember { mutableStateOf(repo.profile().autoCompanyMode) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Text("新增工作", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("把一个业务问题、任务、流程或想法创建成工作，后续持续补充资料并让 AI 生成方案。", color = Muted)
        OutlinedTextField(title, { title = it }, label = { Text("工作名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(description, { description = it }, label = { Text("用大白话描述需求") }, minLines = 5, modifier = Modifier.fillMaxWidth())
        SelectorCard("专业程度", listOf("beginner" to "完全小白", "business" to "懂业务不懂技术", "product" to "产品/项目经理", "developer" to "技术人员", "auto" to "AI 自动判断"), level) { level = it }
        SelectorCard("数据存储方式", listOf("hybrid" to "混合模式（推荐）", "local_only" to "本地模式", "cloud" to "云端模式", "temporary" to "临时分析模式"), storage) { storage = it }
        SelectorCard("原始文件保留时间", listOf("keep_forever" to "长期保留", "delete_after_analysis" to "分析后删除", "delete_after_1_day" to "1 天后删除", "delete_after_7_days" to "7 天后删除", "delete_after_30_days" to "30 天后删除"), retention) { retention = it }
        ToggleRow("允许第三方 AI 分析", allowAi) { allowAi = it }; ToggleRow("自动脱敏后再分析", desensitize) { desensitize = it }; ToggleRow("创建后启动公司自动运营", auto) { auto = it }
        Button(enabled = title.isNotBlank(), onClick = { onDone(repo.createProject(title, description, level, storage, retention, allowAi, desensitize, auto).id) }, modifier = Modifier.fillMaxWidth()) { Text("创建工作") }
    }
}

@Composable
private fun ProjectDetailScreen(repo: AppRepository, projectId: Long, revision: Int, onChanged: () -> Unit, onGroup: (Long) -> Unit, snackbar: SnackbarHostState) {
    val context = LocalContext.current; val scope = rememberCoroutineScope(); val project = repo.project(projectId) ?: return
    var note by remember { mutableStateOf("") }; var busy by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching { context.contentResolver.takePersistableUriPermission(uri, android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            val meta = queryDocument(context, uri); repo.addAsset(projectId, meta.first, uri, meta.second, meta.third); onChanged()
        }
    }
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Card { Column(Modifier.padding(16.dp)) { Row { Text(project.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f)); StatusPill(project.status) }; Text(project.description, color = Muted, modifier = Modifier.padding(top = 8.dp)); Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.SpaceBetween) { Metric("完整度", "${project.requirementScore}%"); Metric("隐私模式", storageLabel(project.storageMode)); Metric("资料", "${repo.assets(projectId).size} 份") } } }
        item { NextActionCard(project, repo.reports(projectId).isNotEmpty(), busy) { busy = true; scope.launch { generateProjectReport(repo, project, snackbar); busy = false; onChanged() } } }
        item { StepCard(1, "补充需求", "把新想法、目标或限制继续告诉 AI。") { OutlinedTextField(note, { note = it }, label = { Text("补充内容") }, modifier = Modifier.fillMaxWidth()); Button(enabled = note.isNotBlank(), onClick = { repo.addNote(projectId, note); note = ""; onChanged() }, modifier = Modifier.padding(top = 8.dp)) { Text("记录") }; repo.notes(projectId).forEach { Text("• ${it.content}", color = Muted, modifier = Modifier.padding(top = 6.dp)) } } }
        item { StepCard(2, "上传并分析资料", "资料保存在当前设备，分析请求通过你的 FDEX 服务端调用 AI。") { Button(onClick = { launcher.launch(arrayOf("*/*")) }) { Text("选择文件") }; repo.assets(projectId).forEach { asset -> AssetRow(asset, busy = busy, onPrivacy = { decision -> repo.updateAsset(asset.copy(privacyDecision = decision)); onChanged() }, onAnalyze = { busy = true; scope.launch { analyzeAsset(repo, project, asset, snackbar); busy = false; onChanged() } }) } } }
        item { StepCard(3, "AI 分析结果", "查看每份资料提取出的关键需求、风险和建议。") { val analyzed = repo.assets(projectId).filter { it.analysis.isNotBlank() }; if (analyzed.isEmpty()) Text("暂无分析结果", color = Muted) else analyzed.forEach { Text(it.name, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp)); Text(it.analysis, color = Muted, modifier = Modifier.padding(top = 4.dp)) } } }
        item { StepCard(4, "方案文档", "查看 AI 为当前工作生成的正式方案。") { Button(enabled = !busy, onClick = { busy = true; scope.launch { generateProjectReport(repo, project, snackbar); busy = false; onChanged() } }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "AI 正在生成…" else "让 AI 生成完整方案") }; repo.reports(projectId).forEach { ReportCard(it) } } }
        if (project.autoOperation) item { val g = repo.groups().firstOrNull { it.projectId == project.id }; if (g != null) Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFECFDF5))) { Column(Modifier.padding(16.dp)) { Text("公司自动运营", color = Emerald, fontWeight = FontWeight.Bold); Text("工作群已创建，AI 团队可以在群里协同推进。", color = Muted); Button(onClick = { onGroup(g.id) }, modifier = Modifier.padding(top = 8.dp)) { Text("进入工作群") } } } }
    }
}

private suspend fun analyzeAsset(repo: AppRepository, project: Project, asset: ProjectAsset, snackbar: SnackbarHostState) {
    val system = "你是企业资料分析助手。请输出关键事实、与工作相关的需求、风险、下一步建议。中文简洁回答。"
    val prompt = "工作：${project.title}\n需求：${project.description}\n资料文件：${asset.name}\n类型：${asset.mimeType}\n大小：${asset.size} 字节。请根据工作上下文和资料元信息给出分析建议。"
    when (val result = ClientAiApi.ask(system, prompt)) {
        is AiGatewayResult.Success -> { repo.updateAsset(asset.copy(status = "analyzed", analysis = result.content)); repo.updateProject(project.copy(status = "analyzed", updatedAt = Instant.now().toString())) }
        is AiGatewayResult.Failure -> snackbar.showSnackbar(result.message)
    }
}

private suspend fun generateProjectReport(repo: AppRepository, project: Project, snackbar: SnackbarHostState) {
    val notes = repo.notes(project.id).joinToString("\n") { it.content }; val analyses = repo.assets(project.id).filter { it.analysis.isNotBlank() }.joinToString("\n\n") { "${it.name}: ${it.analysis}" }
    val prompt = "工作名称：${project.title}\n原始需求：${project.description}\n补充信息：$notes\n资料分析：$analyses"
    when (val result = ClientAiApi.ask("你是企业项目顾问。生成结构清晰的中文执行方案，包含目标、现状判断、关键问题、行动步骤、优先级、风险与检查指标。", prompt, 1800)) {
        is AiGatewayResult.Success -> repo.addReport(project.id, "${project.title} · AI 执行方案", result.content)
        is AiGatewayResult.Failure -> snackbar.showSnackbar(result.message)
    }
}

@Composable
private fun EmployeeManageScreen(repo: AppRepository, revision: Int, onAdd: () -> Unit, onChat: (Long) -> Unit, onChanged: () -> Unit) {
    var industry by remember { mutableStateOf(repo.profile().industry) }
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Row(Modifier.fillMaxWidth()) { Button(onClick = onAdd, modifier = Modifier.weight(1f)) { Text("添加员工") }; Spacer(Modifier.width(8.dp)); OutlinedButton(onClick = { repo.bulkAddEmployees(industry); onChanged() }, modifier = Modifier.weight(1f)) { Text("按行业批量添加") } } }
        item { OutlinedTextField(industry, { industry = it }, label = { Text("批量添加行业") }, modifier = Modifier.fillMaxWidth()) }
        items(repo.employees(), key = { it.id }) { e -> Card(Modifier.fillMaxWidth()) { Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) { Avatar(employeeEmoji(e)); Column(Modifier.weight(1f).padding(start = 10.dp).clickable { onChat(e.id) }) { Text("${e.name} · ${e.position}", fontWeight = FontWeight.SemiBold); Text("${e.department}${if (e.materialManager) " · 系统资料员" else ""}", color = Muted, fontSize = 12.sp) }; if (!e.materialManager) TextButton(onClick = { repo.resignEmployee(e.id); onChanged() }) { Text("离职", color = MaterialTheme.colorScheme.error) } } } }
    }
}

@Composable
private fun AddEmployeeScreen(repo: AppRepository, onDone: () -> Unit, snackbar: SnackbarHostState) {
    var name by remember { mutableStateOf("") }; var dep by remember { mutableStateOf("") }; var pos by remember { mutableStateOf("") }; var prompt by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("创建 AI 员工", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        OutlinedTextField(name, { name = it }, label = { Text("员工名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(dep, { dep = it }, label = { Text("部门") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(pos, { pos = it }, label = { Text("职位") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(prompt, { prompt = it }, label = { Text("角色职责 / Prompt") }, minLines = 4, modifier = Modifier.fillMaxWidth())
        Button(enabled = name.isNotBlank() && pos.isNotBlank(), onClick = { repo.addEmployee(name, dep, pos, prompt.ifBlank { "你是公司的$pos，请从岗位角度给出专业、可执行的协助。" }, repo.profile().industry); onDone() }, modifier = Modifier.fillMaxWidth()) { Text("保存员工") }
    }
}

@Composable
private fun NewGroupScreen(repo: AppRepository, onDone: (Long) -> Unit) {
    var name by remember { mutableStateOf("") }; var desc by remember { mutableStateOf("") }; var auto by remember { mutableStateOf(false) }; val selected = remember { mutableStateListOf<Long>() }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("创建工作群", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        OutlinedTextField(name, { name = it }, label = { Text("群名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(desc, { desc = it }, label = { Text("群用途 / 工作说明") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        Text("选择成员", fontWeight = FontWeight.SemiBold)
        repo.employees().forEach { e -> Row(Modifier.fillMaxWidth().clickable { if (selected.contains(e.id)) selected.remove(e.id) else selected.add(e.id) }, verticalAlignment = Alignment.CenterVertically) { Checkbox(selected.contains(e.id), { checked -> if (checked) selected.add(e.id) else selected.remove(e.id) }); Text("${e.name} · ${e.position}") } }
        ToggleRow("自动运营模式", auto) { auto = it }
        Button(enabled = name.isNotBlank(), onClick = { val ids = if (selected.isEmpty()) repo.employees().map { it.id } else selected.toList(); val g = repo.createGroup(name, desc, null, ids, auto); repo.addGroupMessage(g.id, "system", "", "工作群已创建，可以 @员工 或直接安排团队任务。"); onDone(g.id) }, modifier = Modifier.fillMaxWidth()) { Text("创建工作群") }
    }
}

@Composable
private fun GroupChatScreen(repo: AppRepository, groupId: Long, revision: Int, onChanged: () -> Unit, snackbar: SnackbarHostState) {
    val group = repo.group(groupId) ?: return; val scope = rememberCoroutineScope(); var text by remember { mutableStateOf("") }; var busy by remember { mutableStateOf(false) }; val members = group.memberIds.mapNotNull { repo.employee(it) }
    Column(Modifier.fillMaxSize()) {
        Card(Modifier.fillMaxWidth().padding(10.dp), colors = CardDefaults.cardColors(containerColor = Color(0xFFECFDF5))) { Column(Modifier.padding(12.dp)) { Text("👥 ${group.name}", fontWeight = FontWeight.Bold); Text("${members.size} 名成员 · ${if (group.autoMode) "自动运营" else "人工指挥"}", color = Emerald, fontSize = 12.sp); Text(group.description, color = Muted, fontSize = 13.sp) } }
        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) { items(repo.groupMessages(groupId), key = { it.id }) { GroupBubble(it) }; if (busy) item { Text("团队正在处理…", color = Muted) } }
        Row(Modifier.fillMaxWidth().background(Color.White).padding(10.dp), verticalAlignment = Alignment.CenterVertically) { OutlinedTextField(text, { text = it }, placeholder = { Text("@员工 或安排团队任务…") }, modifier = Modifier.weight(1f), maxLines = 4); Spacer(Modifier.width(8.dp)); Button(enabled = text.isNotBlank() && !busy, onClick = { val prompt = text.trim(); text = ""; repo.addGroupMessage(groupId, "user", "我", prompt); onChanged(); busy = true; scope.launch { val target = members.firstOrNull { prompt.contains("@${it.name}") || prompt.contains(it.position) } ?: members.firstOrNull(); val reply = if (target == null) "当前群里还没有 AI 员工。" else when (val result = ClientAiApi.ask("你是工作群里的${target.position} ${target.name}。${target.rolePrompt}。从团队协作角度简洁、可执行地回复。", prompt)) { is AiGatewayResult.Success -> result.content; is AiGatewayResult.Failure -> "暂时无法完成：${result.message}" }; repo.addGroupMessage(groupId, "employee", target?.name.orEmpty(), reply); busy = false; onChanged() } }) { Text("发送") } } }
}

@Composable
private fun GroupBubble(m: GroupMessage) { val user = m.role == "user"; Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) { Card(colors = CardDefaults.cardColors(containerColor = when { user -> Color(0xFF10B981); m.role == "system" -> Color(0xFFE2E8F0); else -> Color.White }), shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth(.82f)) { Column(Modifier.padding(12.dp)) { if (!user && m.employeeName.isNotBlank()) Text(m.employeeName, color = Emerald, fontSize = 11.sp); Text(m.content, color = if (user) Color.White else MaterialTheme.colorScheme.onSurface) } } } }

@Composable
private fun DiscoverScreen() {
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Card(colors = CardDefaults.cardColors(containerColor = Color(0xFF059669))) { Column(Modifier.padding(20.dp)) { Text("内容与经验社区", color = Color.White.copy(alpha = .8f)); Text("发现功能即将上线", color = Color.White, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp)); Text("未来这里可以发布 AI 落地日志、工作经验、行业方案和用户帖子。", color = Color.White.copy(alpha = .9f), modifier = Modifier.padding(top = 8.dp)); FilledTonalButton(onClick = {}, enabled = false, modifier = Modifier.padding(top = 12.dp)) { Text("发动态（即将上线）") } } } }
        items(listOf("💬" to "AI 客服落地经验", "🛒" to "电商自动化工作日志", "📚" to "企业知识库搭建方案")) { (icon, title) -> Card { Column(Modifier.padding(16.dp)) { Text(icon, fontSize = 28.sp); Text(title, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp)); Text("推荐内容占位，后续将展示真实工作经验与行业方案。", color = Muted, modifier = Modifier.padding(top = 6.dp)) } } }
    }
}

@Composable
private fun MeScreen(repo: AppRepository, revision: Int, onRoute: (Route) -> Unit, onLogout: () -> Unit) {
    val p = repo.profile()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Card { Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) { Avatar("👤", 64); Column(Modifier.padding(start = 14.dp)) { Text(p.companyName.ifBlank { "我的 AI 公司" }, fontWeight = FontWeight.Bold, fontSize = 20.sp); Text(p.name); Text(p.industry.ifBlank { "未设置公司行业" }, color = Emerald); Text(p.email, color = Muted, fontSize = 13.sp) } } } }
        item { MenuCard(listOf("👤" to "账号信息", "🤖" to "AI 员工管理", "🔐" to "隐私与安全", "🗑️" to "最近删除", "⚙️" to "设置")) { label -> when (label) { "账号信息" -> onRoute(Route.Account); "AI 员工管理" -> onRoute(Route.Employees); "最近删除" -> onRoute(Route.Deleted); "设置" -> onRoute(Route.Settings); "隐私与安全" -> onRoute(Route.Settings) } } }
        item { MenuCard(listOf("📖" to "使用说明", "📄" to "隐私条款", "ℹ️" to "关于我们", "✉️" to "联系我们")) { onRoute(Route.About) } }
        item { Card { Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) { Text("软件版本"); Text("v${BuildConfig.VERSION_NAME}", color = Muted) } } }
        item { OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) { Text("退出登录", color = MaterialTheme.colorScheme.error) } }
    }
}

@Composable
private fun AccountScreen(repo: AppRepository, onChanged: () -> Unit, snackbar: SnackbarHostState) {
    val scope = rememberCoroutineScope(); val current = repo.profile(); var name by remember { mutableStateOf(current.name) }; var company by remember { mutableStateOf(current.companyName) }; var industry by remember { mutableStateOf(current.industry) }; var level by remember { mutableStateOf(current.professionalLevel) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        OutlinedTextField(name, { name = it }, label = { Text("姓名") }, modifier = Modifier.fillMaxWidth()); OutlinedTextField(company, { company = it }, label = { Text("公司名称") }, modifier = Modifier.fillMaxWidth()); OutlinedTextField(industry, { industry = it }, label = { Text("公司行业") }, modifier = Modifier.fillMaxWidth()); SelectorCard("专业程度", listOf("beginner" to "完全小白", "business" to "懂业务不懂技术", "product" to "产品/项目经理", "developer" to "技术人员", "auto" to "AI 自动判断"), level) { level = it }
        Button(onClick = { repo.updateProfile(current.copy(name = name.trim(), companyName = company.trim(), industry = industry.trim(), professionalLevel = level)); onChanged(); scope.launch { snackbar.showSnackbar("账号信息已保存") } }, modifier = Modifier.fillMaxWidth()) { Text("保存") }
    }
}

@Composable
private fun SettingsScreen(repo: AppRepository, revision: Int, onAbout: () -> Unit, onChanged: () -> Unit) {
    val p = repo.profile(); var auto by remember { mutableStateOf(p.autoCompanyMode) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card { Column(Modifier.padding(16.dp)) { Text("公司自动化", fontWeight = FontWeight.Bold); Text("新建工作时默认启动公司自动运营模式。", color = Muted, modifier = Modifier.padding(vertical = 8.dp)); ToggleRow("默认启动自动运营", auto) { auto = it; repo.updateProfile(p.copy(autoCompanyMode = it)); onChanged() } } }
        Card(Modifier.fillMaxWidth().clickable(onClick = onAbout)) { Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.SpaceBetween) { Column { Text("关于与版本更新", fontWeight = FontWeight.SemiBold); Text("当前 v${BuildConfig.VERSION_NAME}", color = Muted, fontSize = 12.sp) }; Text("›", fontSize = 24.sp) } }
        Card { Column(Modifier.padding(16.dp)) { Text("隐私与数据", fontWeight = FontWeight.Bold); Text("工作、员工、聊天和群组数据默认保存在当前设备。AI 请求经 fdex.k2n.cn 转发，第三方 API Key 只保存在服务端。", color = Muted, modifier = Modifier.padding(top = 8.dp)) } }
    }
}

@Composable
private fun DeletedScreen(repo: AppRepository, revision: Int, onChanged: () -> Unit) {
    val deleted = repo.allDeletedMessages()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item { Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) { Text("已删除聊天消息 ${deleted.size} 条", modifier = Modifier.weight(1f), color = Muted); if (deleted.isNotEmpty()) Button(onClick = { repo.restoreDeletedMessages(); onChanged() }) { Text("全部恢复") } } }
        if (deleted.isEmpty()) item { EmptyCard("🗑️", "最近删除为空", "清空员工聊天记录后，可在这里恢复。", null, null) }
        items(deleted, key = { it.id }) { m -> Card { Column(Modifier.padding(12.dp)) { Text(repo.employee(m.employeeId)?.name ?: "AI 员工", fontWeight = FontWeight.SemiBold); Text(m.content, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis) } } }
    }
}

@Composable
private fun AboutScreen(serverStatus: String, updateChecking: Boolean, onCheckUpdate: () -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) { Text("FDEX", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold); InfoRow("版本名称", BuildConfig.VERSION_NAME); InfoRow("版本号", BuildConfig.VERSION_CODE.toString()); InfoRow("构建提交", BuildConfig.GIT_SHA); InfoRow("服务端", BuildConfig.SERVER_BASE_URL); InfoRow("服务状态", serverStatus); InfoRow("更新来源", "GitHub Releases") } }
        Button(onClick = onCheckUpdate, enabled = !updateChecking, modifier = Modifier.fillMaxWidth()) { if (updateChecking) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp) else Text("检查更新") }
        Text("新版本由 GitHub Release 提供签名 APK。应用内更新会沿用当前正式签名。", color = Muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun SelectorCard(title: String, options: List<Pair<String, String>>, selected: String, onSelect: (String) -> Unit) { Card { Column(Modifier.padding(14.dp)) { Text(title, fontWeight = FontWeight.SemiBold); options.forEach { (value, label) -> Row(Modifier.fillMaxWidth().clickable { onSelect(value) }.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) { Checkbox(selected == value, { if (it) onSelect(value) }); Text(label) } } } } }
@Composable private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) { Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) { Text(label, modifier = Modifier.weight(1f)); Switch(checked, onChange) } }
@Composable private fun SectionTitle(text: String) { Text(text, fontWeight = FontWeight.SemiBold, color = Muted, modifier = Modifier.padding(vertical = 4.dp)) }
@Composable private fun Avatar(icon: String, size: Int = 48) { Box(Modifier.size(size.dp).background(Color(0xFFD1FAE5), RoundedCornerShape(14.dp)), contentAlignment = Alignment.Center) { Text(icon, fontSize = (size / 2).sp) } }
private fun employeeEmoji(e: Employee) = when { e.materialManager -> "📚"; e.position.contains("研究") -> "🔎"; e.position.contains("执行") || e.position.contains("运营") -> "📋"; e.position.contains("销售") -> "🤝"; else -> "🤖" }
@Composable private fun StatusPill(status: String) { val label = when (status) { "generated" -> "已生成方案"; "analyzed" -> "已分析"; else -> "进行中" }; Text(label, color = Emerald, fontSize = 11.sp, modifier = Modifier.background(Color(0xFFD1FAE5), CircleShape).padding(horizontal = 9.dp, vertical = 4.dp)) }
private fun storageLabel(value: String) = when (value) { "local_only" -> "本地模式"; "cloud" -> "云端模式"; "temporary" -> "临时分析"; else -> "混合模式" }
@Composable private fun Metric(label: String, value: String) { Column { Text(label, fontSize = 11.sp, color = Muted); Text(value, fontWeight = FontWeight.SemiBold, fontSize = 13.sp) } }
@Composable private fun InfoRow(label: String, value: String) { Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text(label, color = Muted); Text(value, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth(.62f)) } }

@Composable
private fun StepCard(step: Int, title: String, desc: String, content: @Composable ColumnScope.() -> Unit) { Card { Column(Modifier.padding(16.dp)) { Row(verticalAlignment = Alignment.Top) { Box(Modifier.size(28.dp).background(Blue, CircleShape), contentAlignment = Alignment.Center) { Text(step.toString(), color = Color.White, fontWeight = FontWeight.Bold) }; Column(Modifier.padding(start = 10.dp)) { Text(title, fontWeight = FontWeight.Bold); Text(desc, color = Muted, fontSize = 13.sp) } }; Column(Modifier.padding(top = 12.dp), content = content) } } }

@Composable
private fun NextActionCard(project: Project, hasReport: Boolean, busy: Boolean, onGenerate: () -> Unit) { val analyzed = project.status == "analyzed"; Card(colors = CardDefaults.cardColors(containerColor = Blue)) { Column(Modifier.padding(18.dp)) { Text("建议下一步", color = Color.White.copy(alpha = .75f), fontSize = 11.sp); Text(if (hasReport) "方案已经准备好" else if (analyzed) "资料分析完成，生成完整方案吧" else "先补充需求或上传资料", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 20.sp, modifier = Modifier.padding(top = 6.dp)); Text(if (hasReport) "你可以查看方案文档，也可以继续补充信息让 AI 优化。" else if (analyzed) "AI 已提取资料要点，下一步生成可执行方案。" else "信息越完整，生成的方案越准确。", color = Color.White.copy(alpha = .9f), modifier = Modifier.padding(top = 6.dp)); if (analyzed && !hasReport) FilledTonalButton(onClick = onGenerate, enabled = !busy, modifier = Modifier.padding(top = 12.dp)) { Text(if (busy) "生成中…" else "生成方案文档") } } } }

@Composable
private fun AssetRow(asset: ProjectAsset, busy: Boolean, onPrivacy: (String) -> Unit, onAnalyze: () -> Unit) { var menu by remember { mutableStateOf(false) }; Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC)), modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) { Column(Modifier.padding(12.dp)) { Row(verticalAlignment = Alignment.CenterVertically) { Text("📎", fontSize = 22.sp); Column(Modifier.weight(1f).padding(start = 8.dp)) { Text(asset.name, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis); Text("${asset.size / 1024} KB · ${asset.status}", color = Muted, fontSize = 11.sp) }; TextButton(onClick = { menu = true }) { Text(if (asset.privacyDecision.isBlank()) "隐私处理" else "已${privacyLabel(asset.privacyDecision)}") }; DropdownMenu(menu, { menu = false }) { listOf("desensitize" to "自动脱敏", "temporary" to "临时分析", "local_only" to "仅本地保存", "confirm_upload" to "确认使用原文件").forEach { (v,l) -> DropdownMenuItem({ Text(l) }, { onPrivacy(v); menu = false }) } } }; Button(onClick = onAnalyze, enabled = !busy && asset.privacyDecision != "local_only", modifier = Modifier.fillMaxWidth().padding(top = 6.dp)) { Text(if (asset.analysis.isBlank()) "让 AI 分析" else "重新分析") }; if (asset.analysis.isNotBlank()) Text(asset.analysis, color = Muted, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp)) } } }
private fun privacyLabel(v: String) = when(v) { "desensitize" -> "脱敏"; "temporary" -> "临时分析"; "local_only" -> "本地保存"; else -> "确认使用" }
@Composable private fun ReportCard(report: Report) { Card(colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC)), modifier = Modifier.fillMaxWidth().padding(top = 10.dp)) { Column(Modifier.padding(14.dp)) { Text(report.title, fontWeight = FontWeight.Bold); Text(report.content, color = Muted, modifier = Modifier.padding(top = 8.dp)) } } }

@Composable
private fun EmptyCard(icon: String, title: String, desc: String, action: String?, onClick: (() -> Unit)?) { Card(Modifier.fillMaxWidth()) { Column(Modifier.fillMaxWidth().padding(28.dp), horizontalAlignment = Alignment.CenterHorizontally) { Text(icon, fontSize = 36.sp); Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp)); Text(desc, color = Muted, modifier = Modifier.padding(top = 6.dp)); if (action != null && onClick != null) Button(onClick = onClick, modifier = Modifier.padding(top = 12.dp)) { Text(action) } } } }

@Composable
private fun MenuCard(items: List<Pair<String,String>>, onClick: (String) -> Unit) { Card { Column { items.forEachIndexed { index, (icon,label) -> Row(Modifier.fillMaxWidth().clickable { onClick(label) }.padding(15.dp), verticalAlignment = Alignment.CenterVertically) { Text(icon); Text(label, modifier = Modifier.weight(1f).padding(start = 10.dp)); Text("›", color = Muted, fontSize = 22.sp) }; if (index != items.lastIndex) Divider() } } } }

private fun queryDocument(context: Context, uri: Uri): Triple<String, Long, String> {
    var name = "资料"; var size = 0L
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) { val ni = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME); val si = cursor.getColumnIndex(OpenableColumns.SIZE); if (ni >= 0) name = cursor.getString(ni) ?: name; if (si >= 0 && !cursor.isNull(si)) size = cursor.getLong(si) }
    }
    return Triple(name, size, context.contentResolver.getType(uri) ?: "application/octet-stream")
}
