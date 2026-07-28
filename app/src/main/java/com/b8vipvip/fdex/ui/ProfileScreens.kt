package com.b8vipvip.fdex.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.AppRepository
import kotlinx.coroutines.launch

@Composable
internal fun DiscoverScreen() {
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Column(Modifier.padding(20.dp)) {
                    Text("内容与经验社区", color = Emerald)
                    Text("发现功能即将上线", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp))
                    Text("未来这里可以发布 AI 落地日志、工作经验、行业方案和用户帖子。", color = Muted, modifier = Modifier.padding(top = 8.dp))
                    FilledTonalButton(onClick = {}, enabled = false, modifier = Modifier.padding(top = 12.dp)) { Text("发动态（即将上线）") }
                }
            }
        }
        items(listOf("💬" to "AI 客服落地经验", "🛒" to "电商自动化工作日志", "📚" to "企业知识库搭建方案")) { (icon, title) ->
            Card {
                Column(Modifier.padding(16.dp)) {
                    Text(icon, fontSize = 28.sp)
                    Text(title, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp))
                    Text("推荐内容占位，后续将展示真实工作经验与行业方案。", color = Muted, modifier = Modifier.padding(top = 6.dp))
                }
            }
        }
    }
}

@Composable
internal fun MeScreen(
    repo: AppRepository,
    revision: Int,
    onAccount: () -> Unit,
    onEmployees: () -> Unit,
    onDeleted: () -> Unit,
    onSettings: () -> Unit,
    onAbout: () -> Unit,
    onLogout: () -> Unit,
) {
    revision.hashCode()
    val profile = repo.profile()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Avatar("👤", 64)
                    Column(Modifier.padding(start = 14.dp)) {
                        Text(profile.companyName.ifBlank { "我的 AI 公司" }, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text(profile.name)
                        Text(profile.industry.ifBlank { "未设置公司行业" }, color = Emerald)
                        Text(profile.email, color = Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        item {
            MenuCard(
                listOf(
                    "👤" to "账号信息",
                    "🤖" to "AI 员工管理",
                    "🔐" to "隐私与安全",
                    "🗑️" to "最近删除",
                    "⚙️" to "设置",
                ),
            ) { label ->
                when (label) {
                    "账号信息" -> onAccount()
                    "AI 员工管理" -> onEmployees()
                    "最近删除" -> onDeleted()
                    "设置", "隐私与安全" -> onSettings()
                }
            }
        }
        item {
            MenuCard(
                listOf("📖" to "使用说明", "📄" to "隐私条款", "ℹ️" to "关于我们", "✉️" to "联系我们"),
            ) { onAbout() }
        }
        item {
            Card {
                Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("软件版本")
                    Text("v${BuildConfig.VERSION_NAME}", color = Muted)
                }
            }
        }
        item {
            OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) {
                Text("退出登录", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
internal fun AccountScreen(repo: AppRepository, onChanged: () -> Unit, snackbar: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    val current = repo.profile()
    var name by remember { mutableStateOf(current.name) }
    var company by remember { mutableStateOf(current.companyName) }
    var industry by remember { mutableStateOf(current.industry) }
    var level by remember { mutableStateOf(current.professionalLevel) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        OutlinedTextField(name, { name = it }, label = { Text("姓名") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(company, { company = it }, label = { Text("公司名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(industry, { industry = it }, label = { Text("公司行业") }, modifier = Modifier.fillMaxWidth())
        SelectorCard(
            "专业程度",
            listOf("beginner" to "完全小白", "business" to "懂业务不懂技术", "product" to "产品/项目经理", "developer" to "技术人员", "auto" to "AI 自动判断"),
            level,
        ) { level = it }
        Button(
            onClick = {
                repo.updateProfile(current.copy(name = name.trim(), companyName = company.trim(), industry = industry.trim(), professionalLevel = level))
                onChanged()
                scope.launch { snackbar.showSnackbar("账号信息已保存") }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("保存") }
    }
}

@Composable
internal fun SettingsScreen(repo: AppRepository, revision: Int, onAbout: () -> Unit, onChanged: () -> Unit) {
    revision.hashCode()
    val profile = repo.profile()
    var auto by remember { mutableStateOf(profile.autoCompanyMode) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp)) {
                Text("公司自动化", fontWeight = FontWeight.Bold)
                Text("新建工作时默认启动公司自动运营模式。", color = Muted, modifier = Modifier.padding(vertical = 8.dp))
                ToggleRow("默认启动自动运营", auto) {
                    auto = it
                    repo.updateProfile(profile.copy(autoCompanyMode = it))
                    onChanged()
                }
            }
        }
        Card(Modifier.fillMaxWidth().clickable(onClick = onAbout)) {
            Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                Column {
                    Text("关于与版本更新", fontWeight = FontWeight.SemiBold)
                    Text("当前 v${BuildConfig.VERSION_NAME}", color = Muted)
                }
                Text("›", fontSize = 24.sp)
            }
        }
        Card {
            Column(Modifier.padding(16.dp)) {
                Text("隐私与数据", fontWeight = FontWeight.Bold)
                Text(
                    "工作、员工、聊天和群组数据默认保存在当前设备。AI 请求经 fdex.k2n.cn 转发，第三方 API Key 只保存在服务端。",
                    color = Muted,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
    }
}

@Composable
internal fun DeletedScreen(repo: AppRepository, revision: Int, onChanged: () -> Unit) {
    revision.hashCode()
    val deleted = repo.allDeletedMessages()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("已删除聊天消息 ${deleted.size} 条", modifier = Modifier.weight(1f), color = Muted)
                if (deleted.isNotEmpty()) Button(onClick = { repo.restoreDeletedMessages(); onChanged() }) { Text("全部恢复") }
            }
        }
        if (deleted.isEmpty()) item { EmptyCard("🗑️", "最近删除为空", "清空员工聊天记录后，可在这里恢复。") }
        items(deleted, key = { it.id }) { message ->
            Card {
                Column(Modifier.padding(12.dp)) {
                    Text(repo.employee(message.employeeId)?.name ?: "AI 员工", fontWeight = FontWeight.SemiBold)
                    Text(message.content, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
internal fun AboutScreen(serverStatus: String, updateChecking: Boolean, onCheckUpdate: () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("FDEX", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                InfoRow("版本名称", BuildConfig.VERSION_NAME)
                InfoRow("版本号", BuildConfig.VERSION_CODE.toString())
                InfoRow("构建提交", BuildConfig.GIT_SHA)
                InfoRow("服务端", BuildConfig.SERVER_BASE_URL)
                InfoRow("服务状态", serverStatus)
                InfoRow("更新来源", "GitHub Releases")
            }
        }
        Button(onClick = onCheckUpdate, enabled = !updateChecking, modifier = Modifier.fillMaxWidth()) {
            if (updateChecking) CircularProgressIndicator() else Text("检查更新")
        }
        Text("新版本由 GitHub Release 提供签名 APK。应用内更新会沿用当前正式签名。", color = Muted)
    }
}
