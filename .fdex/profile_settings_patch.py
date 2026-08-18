from pathlib import Path

ROOT = Path('.')


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'missing patch anchor in {path}: {old[:120]!r}')
    target.write_text(text.replace(old, new, 1), encoding='utf-8')


write('app/src/main/java/com/b8vipvip/fdex/data/ClientPreferences.kt', r'''package com.b8vipvip.fdex.data

import android.content.Context

/** User-controlled client/privacy preferences that must have real product effects. */
class ClientPreferences(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun phone(): String = prefs.getString(KEY_PHONE, "").orEmpty()
    fun setPhone(value: String) = prefs.edit().putString(KEY_PHONE, value.trim()).apply()

    fun autoArchiveKnowledge(): Boolean = prefs.getBoolean(KEY_AUTO_ARCHIVE_KNOWLEDGE, true)
    fun setAutoArchiveKnowledge(value: Boolean) = prefs.edit().putBoolean(KEY_AUTO_ARCHIVE_KNOWLEDGE, value).apply()

    fun remoteLongTermMemory(): Boolean = prefs.getBoolean(KEY_REMOTE_LONG_TERM_MEMORY, true)
    fun setRemoteLongTermMemory(value: Boolean) = prefs.edit().putBoolean(KEY_REMOTE_LONG_TERM_MEMORY, value).apply()

    fun defaultHome(): String = prefs.getString(KEY_DEFAULT_HOME, HOME_MESSAGES)
        .orEmpty()
        .takeIf { it in HOME_VALUES }
        ?: HOME_MESSAGES
    fun setDefaultHome(value: String) {
        prefs.edit().putString(KEY_DEFAULT_HOME, value.takeIf { it in HOME_VALUES } ?: HOME_MESSAGES).apply()
    }

    fun showReasoning(): Boolean = prefs.getBoolean(KEY_SHOW_REASONING, true)
    fun setShowReasoning(value: Boolean) = prefs.edit().putBoolean(KEY_SHOW_REASONING, value).apply()

    fun autoScrollChat(): Boolean = prefs.getBoolean(KEY_AUTO_SCROLL_CHAT, true)
    fun setAutoScrollChat(value: Boolean) = prefs.edit().putBoolean(KEY_AUTO_SCROLL_CHAT, value).apply()

    companion object {
        const val HOME_MESSAGES = "messages"
        const val HOME_KNOWLEDGE = "knowledge"
        const val HOME_DISCOVER = "discover"
        const val HOME_ME = "me"
        val HOME_VALUES = setOf(HOME_MESSAGES, HOME_KNOWLEDGE, HOME_DISCOVER, HOME_ME)

        private const val PREFS_NAME = "fdex_client_preferences_v1"
        private const val KEY_PHONE = "profile_phone"
        private const val KEY_AUTO_ARCHIVE_KNOWLEDGE = "privacy_auto_archive_knowledge"
        private const val KEY_REMOTE_LONG_TERM_MEMORY = "privacy_remote_long_term_memory"
        private const val KEY_DEFAULT_HOME = "client_default_home"
        private const val KEY_SHOW_REASONING = "client_show_reasoning"
        private const val KEY_AUTO_SCROLL_CHAT = "client_auto_scroll_chat"
    }
}
''')

write('app/src/main/java/com/b8vipvip/fdex/data/AccountSecurityManager.kt', r'''package com.b8vipvip.fdex.data

import android.content.Context
import java.security.MessageDigest

/** Password changes stay local and reuse the device-bound credential format used by login. */
internal class AccountSecurityManager(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("fdex_app_v2", Context.MODE_PRIVATE)
    private val credentials = LocalCredentialStore()

    fun changePassword(currentPassword: String, newPassword: String, confirmPassword: String): Result<Unit> {
        if (currentPassword.isBlank()) return Result.failure(IllegalArgumentException("请输入当前密码"))
        if (newPassword.length < 8) return Result.failure(IllegalArgumentException("新密码至少 8 位"))
        if (newPassword != confirmPassword) return Result.failure(IllegalArgumentException("两次输入的新密码不一致"))
        if (currentPassword == newPassword) return Result.failure(IllegalArgumentException("新密码不能与当前密码相同"))

        val verified = runCatching { verifyCurrent(currentPassword) }.getOrDefault(false)
        if (!verified) return Result.failure(IllegalArgumentException("当前密码不正确"))

        return runCatching {
            val record = credentials.createRecord(newPassword)
            prefs.edit()
                .putString(LocalCredentialStore.PREF_PASSWORD_RECORD, record)
                .remove(LocalCredentialStore.LEGACY_PASSWORD_HASH)
                .apply()
        }.map { Unit }
    }

    private fun verifyCurrent(password: String): Boolean {
        val modern = prefs.getString(LocalCredentialStore.PREF_PASSWORD_RECORD, "").orEmpty()
        if (modern.isNotBlank()) return credentials.verify(modern, password)
        val legacy = prefs.getString(LocalCredentialStore.LEGACY_PASSWORD_HASH, "").orEmpty()
        return legacy.isNotBlank() && MessageDigest.isEqual(
            legacy.toByteArray(Charsets.UTF_8),
            legacyHash(password).toByteArray(Charsets.UTF_8),
        )
    }

    private fun legacyHash(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
''')

write('app/src/main/java/com/b8vipvip/fdex/update/UpdatePreferences.kt', r'''package com.b8vipvip.fdex.update

import android.content.Context

object UpdatePreferences {
    private const val PREFS_NAME = "fdex_update_preferences"
    private const val KEY_LAST_CHECK_AT = "last_check_at"
    private const val KEY_AUTO_CHECK = "auto_check"
    private const val CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000L

    fun shouldCheckOnLaunch(context: Context, now: Long = System.currentTimeMillis()): Boolean {
        if (!automaticCheckEnabled(context)) return false
        val lastCheckedAt = lastCheckAt(context)
        return now - lastCheckedAt >= CHECK_INTERVAL_MS
    }

    fun automaticCheckEnabled(context: Context): Boolean = context
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getBoolean(KEY_AUTO_CHECK, true)

    fun setAutomaticCheckEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_AUTO_CHECK, enabled)
            .apply()
    }

    fun lastCheckAt(context: Context): Long = context
        .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getLong(KEY_LAST_CHECK_AT, 0L)

    fun recordCheck(context: Context, now: Long = System.currentTimeMillis()) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putLong(KEY_LAST_CHECK_AT, now)
            .apply()
    }
}
''')

write('app/src/main/java/com/b8vipvip/fdex/ui/ProfileScreens.kt', r'''package com.b8vipvip.fdex.ui

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
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.BuildConfig
import com.b8vipvip.fdex.data.AccountSecurityManager
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ClientPreferences
import com.b8vipvip.fdex.update.UpdatePreferences
import java.text.DateFormat
import java.util.Date
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
                        Text(profile.companyName.ifBlank { "我的 AI 公司" }, color = Emerald)
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
                    "🤖" to "AI 员工管理",
                    "🗑️" to "最近删除",
                ),
            ) { label ->
                when (label) {
                    "账号信息" -> onAccount()
                    "隐私与安全" -> onPrivacy()
                    "AI 员工管理" -> onEmployees()
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
        item {
            OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) {
                Text("退出登录", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
internal fun AccountScreen(repo: AppRepository, onChanged: () -> Unit, snackbar: SnackbarHostState) {
    val context = LocalContext.current
    val prefs = remember { ClientPreferences(context) }
    val scope = rememberCoroutineScope()
    val current = repo.profile()
    var name by remember { mutableStateOf(current.name) }
    var phone by remember { mutableStateOf(prefs.phone()) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("当前登录账号", fontWeight = FontWeight.Bold)
                InfoRow("账号", current.email.ifBlank { "未设置" })
                InfoRow("登录方式", "邮箱 + 本机密码")
                Text("登录邮箱与本机密码共同用于识别当前账号。", color = Muted)
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("个人信息", fontWeight = FontWeight.Bold)
                OutlinedTextField(name, { name = it }, label = { Text("姓名 / 昵称") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(phone, { phone = it }, label = { Text("手机号") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                OutlinedTextField(
                    value = current.email,
                    onValueChange = {},
                    readOnly = true,
                    label = { Text("邮箱") },
                    supportingText = { Text("当前邮箱同时是登录账号；本版本不在资料页直接修改登录标识。") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = {
                        repo.updateProfile(current.copy(name = name.trim()))
                        prefs.setPhone(phone)
                        onChanged()
                        scope.launch { snackbar.showSnackbar("个人信息已保存") }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("保存个人信息") }
            }
        }
    }
}

@Composable
internal fun PrivacySecurityScreen(onChanged: () -> Unit, snackbar: SnackbarHostState) {
    val context = LocalContext.current
    val prefs = remember { ClientPreferences(context) }
    val security = remember { AccountSecurityManager(context) }
    val scope = rememberCoroutineScope()
    var autoArchive by remember { mutableStateOf(prefs.autoArchiveKnowledge()) }
    var remoteMemory by remember { mutableStateOf(prefs.remoteLongTermMemory()) }
    var currentPassword by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text("个人隐私权限", fontWeight = FontWeight.Bold)
                PreferenceToggle(
                    title = "自动整理聊天到本机知识库",
                    description = "关闭后不再自动回填或新增聊天知识；已有知识仍可手动查看和检索。",
                    checked = autoArchive,
                ) {
                    autoArchive = it
                    prefs.setAutoArchiveKnowledge(it)
                    onChanged()
                }
                PreferenceToggle(
                    title = "启用 MemPalace / Letta 长期记忆",
                    description = "关闭后客户端不再发送远程记忆控制信息，新对话不会进行跨会话远程召回或写入。",
                    checked = remoteMemory,
                ) {
                    remoteMemory = it
                    prefs.setRemoteLongTermMemory(it)
                    onChanged()
                }
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("修改密码", fontWeight = FontWeight.Bold)
                Text("修改前必须验证当前密码；新密码继续使用 PBKDF2 与 Android Keystore 设备密钥保护。", color = Muted)
                OutlinedTextField(
                    currentPassword,
                    { currentPassword = it },
                    label = { Text("当前密码") },
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                OutlinedTextField(
                    newPassword,
                    { newPassword = it },
                    label = { Text("新密码（至少 8 位）") },
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                OutlinedTextField(
                    confirmPassword,
                    { confirmPassword = it },
                    label = { Text("再次输入新密码") },
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                )
                Button(
                    onClick = {
                        val result = security.changePassword(currentPassword, newPassword, confirmPassword)
                        if (result.isSuccess) {
                            currentPassword = ""
                            newPassword = ""
                            confirmPassword = ""
                            scope.launch { snackbar.showSnackbar("密码已修改") }
                        } else {
                            scope.launch { snackbar.showSnackbar(result.exceptionOrNull()?.message ?: "密码修改失败") }
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("修改密码") }
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("数据边界", fontWeight = FontWeight.Bold)
                Text("• 账号密码不以明文保存。", color = Muted)
                Text("• 本机聊天与知识库使用本地数据库保存。", color = Muted)
                Text("• 启用远程长期记忆后，仅按员工 ACL 将获准文本交给 FDEX 记忆服务。", color = Muted)
                Text("• Realtime 语音的长期记忆只使用转写后的文字，不保存语音 PCM/Base64。", color = Muted)
            }
        }
    }
}

@Composable
internal fun SettingsScreen(
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
    var company by remember { mutableStateOf(profile.companyName) }
    var industry by remember { mutableStateOf(profile.industry) }
    var level by remember { mutableStateOf(profile.professionalLevel) }
    var autoCompany by remember { mutableStateOf(profile.autoCompanyMode) }
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
                Text("公司与工作偏好", fontWeight = FontWeight.Bold)
                OutlinedTextField(company, { company = it }, label = { Text("公司名称") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(industry, { industry = it }, label = { Text("公司行业") }, modifier = Modifier.fillMaxWidth())
                SelectorCard(
                    "默认专业程度",
                    listOf(
                        "beginner" to "完全小白",
                        "business" to "懂业务不懂技术",
                        "product" to "产品/项目经理",
                        "developer" to "技术人员",
                        "auto" to "AI 自动判断",
                    ),
                    level,
                ) { level = it }
                PreferenceToggle(
                    title = "默认启动公司自动运营",
                    description = "新建工作时默认开启自动运营模式。",
                    checked = autoCompany,
                ) { autoCompany = it }
                Button(
                    onClick = {
                        repo.updateProfile(
                            profile.copy(
                                companyName = company.trim(),
                                industry = industry.trim(),
                                professionalLevel = level,
                                autoCompanyMode = autoCompany,
                            ),
                        )
                        onChanged()
                        scope.launch { snackbar.showSnackbar("工作偏好已保存") }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("保存工作偏好") }
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
                PreferenceToggle(
                    title = "显示 AI 思考摘要",
                    description = "关闭后仍正常生成答案，但聊天页不展示流式 reasoning 摘要。",
                    checked = showReasoning,
                ) {
                    showReasoning = it
                    prefs.setShowReasoning(it)
                    onChanged()
                }
                PreferenceToggle(
                    title = "回答时自动滚动到底部",
                    description = "流式生成正文时自动跟随最新内容。",
                    checked = autoScroll,
                ) {
                    autoScroll = it
                    prefs.setAutoScrollChat(it)
                    onChanged()
                }
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("版本更新", fontWeight = FontWeight.Bold)
                PreferenceToggle(
                    title = "自动检查新版本",
                    description = "启用后应用启动时最多每 6 小时检查一次；关闭后仍可手动检查。",
                    checked = autoUpdate,
                ) {
                    autoUpdate = it
                    UpdatePreferences.setAutomaticCheckEnabled(context, it)
                    onChanged()
                }
                Text("手动检查与版本详情请从“我的 → 检查更新”进入。", color = Muted)
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
internal fun UpdateScreen(serverStatus: String, updateChecking: Boolean, onCheckUpdate: () -> Unit) {
    val context = LocalContext.current
    val lastChecked = UpdatePreferences.lastCheckAt(context)
    val lastCheckedLabel = if (lastChecked <= 0L) "尚未检查" else DateFormat.getDateTimeInstance().format(Date(lastChecked))
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("关于 FDEX", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                Text("FDEX 是面向个人与小团队的 AI 虚拟公司客户端，提供员工协作、知识库、项目与实时语音能力。", color = Muted)
                InfoRow("版本名称", BuildConfig.VERSION_NAME)
                InfoRow("版本号", BuildConfig.VERSION_CODE.toString())
                InfoRow("构建提交", BuildConfig.GIT_SHA)
                InfoRow("服务端", BuildConfig.SERVER_BASE_URL)
                InfoRow("服务状态", serverStatus)
                InfoRow("更新来源", "GitHub Releases")
                InfoRow("上次检查", lastCheckedLabel)
            }
        }
        Button(onClick = onCheckUpdate, enabled = !updateChecking, modifier = Modifier.fillMaxWidth()) {
            if (updateChecking) CircularProgressIndicator() else Text("检查更新")
        }
        Text("正式更新包由 GitHub Release 提供签名 APK；应用内安装会沿用当前正式签名。", color = Muted)
    }
}

@Composable
internal fun UsageGuideScreen() {
    LazyColumn(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { GuideCard("1", "创建或管理 AI 员工", "在“消息”或“我的 → AI 员工管理”中添加员工，为员工设置部门、职位、角色 Prompt 和访问权限。") }
        item { GuideCard("2", "开始私聊或工作群", "私聊适合明确分工；工作群适合多个员工围绕同一个任务协作。聊天支持文字、图片、文档和实时语音。") }
        item { GuideCard("3", "使用知识库", "聊天可自动整理为分类、摘要和关键词；员工能读取哪些知识和其他员工聊天，由员工权限单独控制。") }
        item { GuideCard("4", "使用 Realtime 实时语音", "语音通话使用同一个实时模型会话保持上下文；长期记忆只保存已经转写并回显的文字，不保存音频格式。") }
        item { GuideCard("5", "排查异常", "优先记录当前版本、复现步骤和界面提示；AI 请求异常时可结合服务端 FDEX_AI 日志中的 request_id 定位。") }
    }
}

@Composable
internal fun PrivacyPolicyScreen() {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("账号与密码", fontWeight = FontWeight.Bold)
                Text("登录密码不会以明文保存。当前客户端使用 PBKDF2 派生密码，并结合 Android Keystore 中不可导出的设备密钥验证。", color = Muted)
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("聊天、知识库与长期记忆", fontWeight = FontWeight.Bold)
                Text("聊天和本机知识库默认保存在当前设备。开启 MemPalace / Letta 后，按员工 ACL 获准的聊天文本可发送到 FDEX 服务端进行跨会话原始历史与结构化记忆管理。", color = Muted)
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("AI 请求", fontWeight = FontWeight.Bold)
                Text("需要 AI 生成时，请求经 FDEX 服务端路由到配置的 AI 供应商。第三方供应商 API Key 由服务端管理，不写入 Android 客户端。", color = Muted)
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("实时语音", fontWeight = FontWeight.Bold)
                Text("实时音频用于当前通话传输与播放；FDEX 长期记忆链路只接收转写后的用户文字与 AI 回复文字，不把 PCM/Base64 音频写入 MemPalace 或 Letta。", color = Muted)
            }
        }
        Text("你可以随时在“隐私与安全”中关闭自动知识归档或远程长期记忆。", color = Muted)
    }
}

@Composable
internal fun ContactScreen() {
    val uriHandler = LocalUriHandler.current
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("技术支持与问题反馈", fontWeight = FontWeight.Bold)
                Text("推荐通过 GitHub Issues 提交问题。请附上 FDEX 版本、复现步骤、相关 request_id 或脱敏日志，不要提交账号密码、API Key 或其他密钥。", color = Muted)
                OutlinedButton(onClick = { uriHandler.openUri("https://github.com/b8vipvip/fdex/issues") }, modifier = Modifier.fillMaxWidth()) {
                    Text("打开 GitHub Issues")
                }
            }
        }
        Card {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("当前服务", fontWeight = FontWeight.Bold)
                InfoRow("客户端版本", "v${BuildConfig.VERSION_NAME}")
                InfoRow("服务端", BuildConfig.SERVER_BASE_URL)
                InfoRow("项目", "b8vipvip/fdex")
            }
        }
    }
}

@Composable
private fun PreferenceToggle(title: String, description: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, fontWeight = FontWeight.Medium)
            Text(description, color = Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 2.dp))
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun GuideCard(step: String, title: String, description: String) {
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
''')

# FdexApp: add independent routes and real client-home preference.
path = 'app/src/main/java/com/b8vipvip/fdex/ui/FdexApp.kt'
text = (ROOT / path).read_text(encoding='utf-8')
text = text.replace('import com.b8vipvip.fdex.data.AppRepository\n', 'import com.b8vipvip.fdex.data.AppRepository\nimport com.b8vipvip.fdex.data.ClientPreferences\n', 1)
text = text.replace('    data object Account : Route\n    data object Settings : Route\n    data object Deleted : Route\n    data object About : Route\n', '    data object Account : Route\n    data object PrivacySecurity : Route\n    data object Settings : Route\n    data object Deleted : Route\n    data object Update : Route\n    data object Guide : Route\n    data object PrivacyPolicy : Route\n    data object Contact : Route\n', 1)
anchor = '''internal fun fallbackBackTarget(route: Route): Route = when (route) {\n    Route.Login -> Route.Login\n    Route.Register -> Route.Login\n    Route.Messages -> Route.Messages\n    else -> Route.Messages\n}\n'''
replacement = anchor + '''\ninternal fun clientHomeRoute(value: String): Route = when (value) {\n    ClientPreferences.HOME_KNOWLEDGE -> Route.Work\n    ClientPreferences.HOME_DISCOVER -> Route.Discover\n    ClientPreferences.HOME_ME -> Route.Me\n    else -> Route.Messages\n}\n'''
if anchor not in text:
    raise SystemExit('missing fallbackBackTarget anchor')
text = text.replace(anchor, replacement, 1)
text = text.replace('    val repo = remember { AppRepository(context) }\n    val scope = rememberCoroutineScope()\n', '    val repo = remember { AppRepository(context) }\n    val clientPreferences = remember { ClientPreferences(context) }\n    val scope = rememberCoroutineScope()\n', 1)
text = text.replace('    var route by remember { mutableStateOf<Route>(if (repo.isLoggedIn()) Route.Messages else Route.Login) }\n', '    var route by remember { mutableStateOf<Route>(if (repo.isLoggedIn()) clientHomeRoute(clientPreferences.defaultHome()) else Route.Login) }\n', 1)
text = text.replace('        Route.Account -> "账号信息"\n        Route.Settings -> "设置"\n        Route.Deleted -> "最近删除"\n        Route.About -> "关于 FDEX"\n', '        Route.Account -> "账号信息"\n        Route.PrivacySecurity -> "隐私与安全"\n        Route.Settings -> "设置"\n        Route.Deleted -> "最近删除"\n        Route.Update -> "检查更新"\n        Route.Guide -> "使用说明"\n        Route.PrivacyPolicy -> "隐私说明"\n        Route.Contact -> "联系我们"\n', 1)
text = text.replace('                Route.Login -> LoginScreen(repo, onLogin = { touch(); history.clear(); route = Route.Messages }, onRegister = { route = Route.Register })\n                Route.Register -> RegisterScreen(repo, onDone = { touch(); history.clear(); route = Route.Messages }, onLogin = { route = Route.Login })\n', '                Route.Login -> LoginScreen(repo, onLogin = { touch(); history.clear(); route = clientHomeRoute(clientPreferences.defaultHome()) }, onRegister = { route = Route.Register })\n                Route.Register -> RegisterScreen(repo, onDone = { touch(); history.clear(); route = clientHomeRoute(clientPreferences.defaultHome()) }, onLogin = { route = Route.Login })\n', 1)
old_me = '''                Route.Me -> MeScreen(\n                    repo,\n                    revision,\n                    onAccount = { go(Route.Account) },\n                    onEmployees = { go(Route.Employees) },\n                    onDeleted = { go(Route.Deleted) },\n                    onSettings = { go(Route.Settings) },\n                    onAbout = { go(Route.About) },\n                    onLogout = { repo.logout(); history.clear(); route = Route.Login; touch() },\n                )\n'''
new_me = '''                Route.Me -> MeScreen(\n                    repo,\n                    revision,\n                    onAccount = { go(Route.Account) },\n                    onPrivacy = { go(Route.PrivacySecurity) },\n                    onEmployees = { go(Route.Employees) },\n                    onDeleted = { go(Route.Deleted) },\n                    onSettings = { go(Route.Settings) },\n                    onUpdate = { go(Route.Update) },\n                    onGuide = { go(Route.Guide) },\n                    onPrivacyPolicy = { go(Route.PrivacyPolicy) },\n                    onContact = { go(Route.Contact) },\n                    onLogout = { repo.logout(); history.clear(); route = Route.Login; touch() },\n                )\n'''
if old_me not in text:
    raise SystemExit('missing MeScreen route anchor')
text = text.replace(old_me, new_me, 1)
text = text.replace('                Route.Account -> AccountScreen(repo, onChanged = { touch() }, snackbar = snackbar)\n                Route.Settings -> SettingsScreen(repo, revision, onAbout = { go(Route.About) }, onChanged = { touch() })\n                Route.Deleted -> DeletedScreen(repo, revision, onChanged = { touch() })\n                Route.About -> AboutScreen(serverStatus, updateChecking) { scope.launch { checkUpdate(true) } }\n', '                Route.Account -> AccountScreen(repo, onChanged = { touch() }, snackbar = snackbar)\n                Route.PrivacySecurity -> PrivacySecurityScreen(onChanged = { touch() }, snackbar = snackbar)\n                Route.Settings -> SettingsScreen(repo, revision, onChanged = { touch() }, snackbar = snackbar)\n                Route.Deleted -> DeletedScreen(repo, revision, onChanged = { touch() })\n                Route.Update -> UpdateScreen(serverStatus, updateChecking) { scope.launch { checkUpdate(true) } }\n                Route.Guide -> UsageGuideScreen()\n                Route.PrivacyPolicy -> PrivacyPolicyScreen()\n                Route.Contact -> ContactScreen()\n', 1)
(ROOT / path).write_text(text, encoding='utf-8')

# KnowledgeStore: make privacy toggles affect actual archive and remote-memory behavior.
path = 'app/src/main/java/com/b8vipvip/fdex/data/KnowledgeStore.kt'
text = (ROOT / path).read_text(encoding='utf-8')
text = text.replace('    private val metaPrefs = appContext.getSharedPreferences("fdex_knowledge_meta_v1", Context.MODE_PRIVATE)\n', '    private val metaPrefs = appContext.getSharedPreferences("fdex_knowledge_meta_v1", Context.MODE_PRIVATE)\n    private val clientPreferences = ClientPreferences(appContext)\n', 1)
text = text.replace('    fun remoteMemoryControl(\n        repo: AppRepository,\n        employee: Employee,\n        conversationId: String,\n    ): String {\n        val permissions = permissionsFor(employee.id)\n', '    fun remoteMemoryControl(\n        repo: AppRepository,\n        employee: Employee,\n        conversationId: String,\n    ): String {\n        if (!clientPreferences.remoteLongTermMemory()) return ""\n        val permissions = permissionsFor(employee.id)\n', 1)
text = text.replace('    fun backfillIfNeeded(repo: AppRepository): Int {\n        val marker = "history_backfilled_${scopeKey(repo)}"\n', '    fun backfillIfNeeded(repo: AppRepository): Int {\n        if (!clientPreferences.autoArchiveKnowledge()) return 0\n        val marker = "history_backfilled_${scopeKey(repo)}"\n', 1)
insert_anchor = '    fun entries(includeArchived: Boolean = false): List<KnowledgeEntry> = database\n'
if insert_anchor not in text:
    raise SystemExit('missing KnowledgeStore entries anchor')
text = text.replace(insert_anchor, '    fun automaticArchiveEnabled(): Boolean = clientPreferences.autoArchiveKnowledge()\n\n' + insert_anchor, 1)
(ROOT / path).write_text(text, encoding='utf-8')

# Streaming chats: honor archive/UI preferences for private and group chats.
path = 'app/src/main/java/com/b8vipvip/fdex/ui/StreamingChatScreens.kt'
text = (ROOT / path).read_text(encoding='utf-8')
text = text.replace('import com.b8vipvip.fdex.data.ChatMessage\n', 'import com.b8vipvip.fdex.data.ChatMessage\nimport com.b8vipvip.fdex.data.ClientPreferences\n', 1)
text = text.replace('    val knowledgeStore = remember { KnowledgeStore(context) }\n    val scope = rememberCoroutineScope()\n', '    val knowledgeStore = remember { KnowledgeStore(context) }\n    val clientPreferences = remember { ClientPreferences(context) }\n    val showReasoning = clientPreferences.showReasoning()\n    val autoScrollChat = clientPreferences.autoScrollChat()\n    val scope = rememberCoroutineScope()\n', 1)
text = text.replace('    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy) {\n        val last = listState.layoutInfo.totalItemsCount - 1\n        if (last >= 0) listState.animateScrollToItem(last)\n    }\n', '    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy, autoScrollChat) {\n        if (autoScrollChat) {\n            val last = listState.layoutInfo.totalItemsCount - 1\n            if (last >= 0) listState.animateScrollToItem(last)\n        }\n    }\n', 1)
text = text.replace('                            reasoning = streamReasoning,\n', '                            reasoning = if (showReasoning) streamReasoning else "",\n', 1)
old = '''                            if (user != null) {\n                                val entry = knowledgeStore.rememberEmployeeExchange(\n                                    repo = repo,\n                                    employeeId = employeeId,\n                                    user = user,\n                                    assistant = stored,\n                                    allowSharing = true,\n                                )\n                                scope.launch {\n                                    KnowledgeOrganizer.enrich(knowledgeStore, entry.id)\n                                    onChanged()\n                                }\n                            }\n'''
new = '''                            if (user != null && knowledgeStore.automaticArchiveEnabled()) {\n                                val entry = knowledgeStore.rememberEmployeeExchange(\n                                    repo = repo,\n                                    employeeId = employeeId,\n                                    user = user,\n                                    assistant = stored,\n                                    allowSharing = true,\n                                )\n                                scope.launch {\n                                    KnowledgeOrganizer.enrich(knowledgeStore, entry.id)\n                                    onChanged()\n                                }\n                            }\n'''
if old not in text:
    raise SystemExit('missing realtime archive anchor')
text = text.replace(old, new, 1)
old = '''                    val knowledge = knowledgeStore.rememberEmployeeExchange(\n                        repo = repo,\n                        employeeId = employeeId,\n                        user = userMessage,\n                        assistant = assistantMessage,\n                        allowSharing = result.content.isNotBlank(),\n                    )\n'''
new = '''                    val knowledge = if (knowledgeStore.automaticArchiveEnabled()) {\n                        knowledgeStore.rememberEmployeeExchange(\n                            repo = repo,\n                            employeeId = employeeId,\n                            user = userMessage,\n                            assistant = assistantMessage,\n                            allowSharing = result.content.isNotBlank(),\n                        )\n                    } else null\n'''
if old not in text:
    raise SystemExit('missing employee archive anchor')
text = text.replace(old, new, 1)
text = text.replace('                    launch {\n                        KnowledgeOrganizer.enrich(knowledgeStore, knowledge.id)\n                        onChanged()\n                    }\n', '                    if (knowledge != null) launch {\n                        KnowledgeOrganizer.enrich(knowledgeStore, knowledge.id)\n                        onChanged()\n                    }\n', 1)
# Second chat screen preference wiring.
needle = '    val knowledgeStore = remember { KnowledgeStore(context) }\n    val scope = rememberCoroutineScope()\n'
if needle not in text:
    raise SystemExit('missing group knowledgeStore anchor')
text = text.replace(needle, '    val knowledgeStore = remember { KnowledgeStore(context) }\n    val clientPreferences = remember { ClientPreferences(context) }\n    val showReasoning = clientPreferences.showReasoning()\n    val autoScrollChat = clientPreferences.autoScrollChat()\n    val scope = rememberCoroutineScope()\n', 1)
needle = '    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy) {\n        val last = listState.layoutInfo.totalItemsCount - 1\n        if (last >= 0) listState.animateScrollToItem(last)\n    }\n'
if needle not in text:
    raise SystemExit('missing group autoscroll anchor')
text = text.replace(needle, '    LaunchedEffect(revision, streamMarkdown.length, streamStatus, busy, autoScrollChat) {\n        if (autoScrollChat) {\n            val last = listState.layoutInfo.totalItemsCount - 1\n            if (last >= 0) listState.animateScrollToItem(last)\n        }\n    }\n', 1)
text = text.replace('                            reasoning = streamReasoning,\n', '                            reasoning = if (showReasoning) streamReasoning else "",\n', 1)
old = '''                    val knowledge = knowledgeStore.rememberGroupExchange(\n                        repo = repo,\n                        groupId = groupId,\n                        targetEmployeeId = target.id,\n                        targetEmployeeName = target.name,\n                        user = userMessage,\n                        assistant = assistantMessage,\n                        allowSharing = result.content.isNotBlank(),\n                    )\n'''
new = '''                    val knowledge = if (knowledgeStore.automaticArchiveEnabled()) {\n                        knowledgeStore.rememberGroupExchange(\n                            repo = repo,\n                            groupId = groupId,\n                            targetEmployeeId = target.id,\n                            targetEmployeeName = target.name,\n                            user = userMessage,\n                            assistant = assistantMessage,\n                            allowSharing = result.content.isNotBlank(),\n                        )\n                    } else null\n'''
if old not in text:
    raise SystemExit('missing group archive anchor')
text = text.replace(old, new, 1)
needle = '                    launch {\n                        KnowledgeOrganizer.enrich(knowledgeStore, knowledge.id)\n                        onChanged()\n                    }\n'
if needle not in text:
    raise SystemExit('missing group enrich anchor')
text = text.replace(needle, '                    if (knowledge != null) launch {\n                        KnowledgeOrganizer.enrich(knowledgeStore, knowledge.id)\n                        onChanged()\n                    }\n', 1)
(ROOT / path).write_text(text, encoding='utf-8')

print('profile/settings center patch applied')
