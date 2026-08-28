package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.CentralSessionStore

@Composable
internal fun CenterAccountScreen(repo: AppRepository) {
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val localProfile = repo.profile()

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("FDEX 中心账号", fontWeight = FontWeight.Bold)
                InfoRow("邮箱", sessions.email().ifBlank { "未登录" })
                InfoRow("User ID", sessions.userId().ifBlank { "-" })
                InfoRow("姓名 / 昵称", sessions.name().ifBlank { localProfile.name.ifBlank { "未设置" } })
                InfoRow("登录方式", "FDEX 中心邮箱 + 中心密码")
                Text(
                    "当前 user_id 是智体、GitHub、Coding Agent 项目、服务端沙箱、长期记忆 namespace 和本机数据库的统一账号归属。",
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("本机资料", fontWeight = FontWeight.Bold)
                InfoRow("本机显示名", localProfile.name.ifBlank { "未设置" })
                Text("这些是当前账号独立 Android 数据库里的个人资料，不再作为登录凭据。密码修改、设备管理与账号注销请进入“隐私与安全”。", color = Muted)
            }
        }
    }
}
