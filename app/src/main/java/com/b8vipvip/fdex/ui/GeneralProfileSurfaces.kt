package com.b8vipvip.fdex.ui

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
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ClientPreferences
import com.b8vipvip.fdex.update.UpdatePreferences
import java.text.DateFormat
import java.util.Date
import kotlinx.coroutines.launch

@Composable
internal fun GeneralDiscoverScreen() {
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Column(Modifier.padding(20.dp)) {
                    Text("内容与经验社区", color = Emerald)
                    Text("发现功能即将上线", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 6.dp))
                    Text("未来这里可以发布学习经验、智体配置、AI 使用方法、创作记录和用户帖子。", color = Muted, modifier = Modifier.padding(top = 8.dp))
                    FilledTonalButton(onClick = {}, enabled = false, modifier = Modifier.padding(top = 12.dp)) { Text("发动态（即将上线）") }
                }
            }
        }
        items(listOf("📚" to "学习型智体使用方法", "🧠" to "长期记忆配置经验", "💻" to "Coding Agent 实践记录")) { (icon, title) ->
            Card {
                Column(Modifier.padding(16.dp)) {
                    Text(icon, fontSize = 28.sp)
                    Text(title, fontWeight = FontWeight.SemiBold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp))
                    Text("推荐内容占位，后续将展示真实用户经验。", color = Muted, modifier = Modifier.padding(top = 6.dp))
                }
            }
        }
    }
}

@Composable
internal fun GeneralMeScreen(
    repo: AppRepository,
    revision: Int,
    onAccount: () -> Unit,
    onPrivacy: () -> Unit,
    onEmployees: () -> Unit,
    onDeleted: () -> Unit,
    onSettings: () -> Unit,
    onUpdate: () -> Unit,
    onGuide: () -> Unit,
    onPrivacyPolicy: () -> Unit,
    onContact: () -> Unit,
    onLogout: () -> Unit,
) {
    revision.hashCode()
    val profile = repo.profile()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Avatar("👤", 64)
                    Column(Modifier.weight(1f).padding(start = 14.dp)) {
                        Text(profile.name.ifBlank { "我" }, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text("FDEX 智体与知识空间", color = Emerald)
                        Text(profile.email.ifBlank { "未设置登录邮箱" }, color = Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        item { SectionTitle("账号与数据") }
        item {
            MenuCard(
                listOf(
                    "👤" to "账号信息",
                    "🔐" to "隐私与安全",
                    "🤖" to "智体管理",
                    "🗑️" to "最近删除",
                ),
            ) { label ->
                when (label) {
                    "账号信息" -> onAccount()
                    "隐私与安全" -> onPrivacy()
                    "智体管理" -> onEmployees()
                    "最近删除" -> onDeleted()
                }
            }
        }
        item { SectionTitle("客户端与支持") }
        item {
            MenuCard(
                listOf(
                    "⚙️" to "设置",
                    "⬆️" to "检查更新",
                    "📖" to "使用说明",
                    "🛡️" to "隐私说明",
                    "✉️" to "联系我们",
                ),
            ) { label ->
                when (label) {
                    "设置" -> onSettings()
                    "检查更新" -> onUpdate()
                    "使用说明" -> onGuide()
                    "隐私说明" -> onPrivacyPolicy()
                    "联系我们" -> onContact()
                }
            }
        }
        item {
            Card {
                Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("FDEX Android")
                    Text("v${BuildConfig.VERSION_NAME}", color = Muted)
                }
            }
        }
        item { OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) { Text("退出登录", color = MaterialTheme.colorScheme.error) } }
    }
}

@Composable
internal fun GeneralSettingsScreen(
    repo: AppRepository,
    revision: Int,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val prefs = remember { ClientPreferences(context) }
    val scope = rememberCoroutineScope()
    val profile = repo.profile()
    var level by remember { mutableStateOf(profile.professionalLevel.ifBlank { "auto" }) }
    var home by remember { mutableStateOf(prefs.defaultHome()) }
    var showReasoning by remember { mutableStateOf(prefs.showReasoning()) }
    var autoScroll by remember { mutableStateOf(prefs.autoScrollChat()) }
    var autoUpdate by remember { mutableStateOf(UpdatePreferences.automaticCheckEnabled(context)) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("AI 与工作偏好", fontWeight = FontWeight.Bold)
                SelectorCard(
                    "默认回答程度",
                    listOf(
                        "beginner" to "更易理解",
                        "business" to "实用清晰",
                        "product" to "结构化分析",
                        "developer" to "技术细节优先",
                        "auto" to "AI 自动判断",
                    ),
                    level,
                ) { level = it }
                Button(
                    onClick = {
                        repo.updateProfile(profile.copy(companyName = "", industry = "", professionalLevel = level, autoCompanyMode = false))
                        onChanged()
                        scope.launch { snackbar.showSnackbar("偏好已保存") }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("保存偏好") }
            }
        }
        SelectorCard(
            "默认打开页面",
            listOf(
                ClientPreferences.HOME_MESSAGES to "消息",
                ClientPreferences.HOME_KNOWLEDGE to "知识库",
                ClientPreferences.HOME_DISCOVER to "发现",
                ClientPreferences.HOME_ME to "我的",
            ),
            home,
        ) {
            home = it
            prefs.setDefaultHome(it)
            onChanged()
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text("聊天体验", fontWeight = FontWeight.Bold)
                GeneralToggle("显示 AI 思考摘要", "关闭后仍正常生成答案，但不展示流式 reasoning 摘要。", showReasoning) {
                    showReasoning = it; prefs.setShowReasoning(it); onChanged()
                }
                GeneralToggle("回答时自动滚动到底部", "流式生成正文时自动跟随最新内容。", autoScroll) {
                    autoScroll = it; prefs.setAutoScrollChat(it); onChanged()
                }
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("版本更新", fontWeight = FontWeight.Bold)
                GeneralToggle("自动检查新版本", "应用启动时最多每 6 小时检查一次；关闭后仍可手动检查。", autoUpdate) {
                    autoUpdate = it; UpdatePreferences.setAutomaticCheckEnabled(context, it); onChanged()
                }
                Text("手动检查与版本详情请从“我的 → 检查更新”进入。", color = Muted)
            }
        }
    }
}

@Composable
private fun GeneralToggle(title: String, description: String, checked: Boolean, onChanged: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(description, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onChanged)
    }
}

@Composable
internal fun GeneralDeletedScreen(repo: AppRepository, revision: Int, onChanged: () -> Unit) {
    revision.hashCode()
    val deleted = repo.allDeletedMessages()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("已删除聊天消息 ${deleted.size} 条", modifier = Modifier.weight(1f), color = Muted)
                if (deleted.isNotEmpty()) Button(onClick = { repo.restoreDeletedMessages(); onChanged() }) { Text("全部恢复") }
            }
        }
        if (deleted.isEmpty()) item { EmptyCard("🗑️", "最近删除为空", "清空智体聊天记录后，可在这里恢复。") }
        items(deleted, key = { it.id }) { message ->
            Card {
                Column(Modifier.padding(12.dp)) {
                    Text(repo.employee(message.employeeId)?.name ?: "智体", fontWeight = FontWeight.SemiBold)
                    Text(message.content, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
internal fun GeneralUpdateScreen(serverStatus: String, updateChecking: Boolean, onCheckUpdate: () -> Unit) {
    val context = LocalContext.current
    val lastChecked = UpdatePreferences.lastCheckAt(context)
    val label = if (lastChecked <= 0L) "尚未检查" else DateFormat.getDateTimeInstance().format(Date(lastChecked))
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("关于 FDEX", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text("FDEX 是通用的个人 AI 智体客户端，提供自定义身份、协作聊天、知识库、项目、Coding Agent 与实时语音能力。", color = Muted)
                InfoRow("版本名称", BuildConfig.VERSION_NAME)
                InfoRow("版本号", BuildConfig.VERSION_CODE.toString())
                InfoRow("构建提交", BuildConfig.GIT_SHA)
                InfoRow("服务状态", serverStatus)
                InfoRow("上次检查", label)
            }
        }
        Button(onClick = onCheckUpdate, enabled = !updateChecking, modifier = Modifier.fillMaxWidth()) {
            if (updateChecking) CircularProgressIndicator() else Text("检查更新")
        }
    }
}

@Composable
internal fun GeneralUsageGuideScreen() {
    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { GeneralGuideCard("1", "创建智体", "在“消息”或“我的 → 智体管理”中添加智体。创建时只需填写身份定义提示词，也可以留空。") }
        item { GeneralGuideCard("2", "定义任意身份", "智体可以是语文老师、数学老师、体育老师、学习伙伴、生活助手、创作伙伴或其他你需要的角色。") }
        item { GeneralGuideCard("3", "私聊与工作群", "可以单独与智体聊天，也可以把多个智体加入工作群协作。聊天支持文字、图片、文档和实时语音。") }
        item { GeneralGuideCard("4", "使用知识与记忆", "聊天可整理为摘要和关键词；每个智体能读取哪些知识和其他智体聊天，由权限单独控制。") }
        item { GeneralGuideCard("5", "Coding Agent", "需要处理代码与 GitHub 时，可把指定智体启用为 Coding Agent；普通智体不会因此获得代码仓库权限。") }
    }
}

@Composable
private fun GeneralGuideCard(step: String, title: String, description: String) {
    Card {
        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.Top) {
            Text(step, color = Emerald, fontWeight = FontWeight.Bold, fontSize = 20.sp)
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(title, fontWeight = FontWeight.Bold)
                Text(description, color = Muted, modifier = Modifier.padding(top = 4.dp))
            }
        }
    }
}

@Composable
internal fun GeneralPrivacyPolicyScreen() {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("账号与密码", fontWeight = FontWeight.Bold)
            Text("登录密码不会以明文保存；账号数据按 FDEX user_id 隔离。", color = Muted)
        } }
        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("聊天、知识库与长期记忆", fontWeight = FontWeight.Bold)
            Text("聊天和本机知识库默认保存在当前设备。开启 MemPalace / Letta 后，仅按智体 ACL 将获准的聊天文本发送到 FDEX 服务端进行跨会话记忆管理。", color = Muted)
        } }
        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("AI 请求与实时语音", fontWeight = FontWeight.Bold)
            Text("AI 请求由 FDEX 服务端路由到配置的供应商。实时音频用于当前通话；长期记忆链路只接收已转写文字，不保存 PCM/Base64 音频。", color = Muted)
        } }
        Text("你可以随时在“隐私与安全”中关闭自动知识归档或远程长期记忆。", color = Muted)
    }
}
