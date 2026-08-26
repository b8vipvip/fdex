package com.b8vipvip.fdex.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.BuildConfig
import kotlinx.coroutines.launch


@Composable
internal fun GitHubProjectSetup(
    snackbar: SnackbarHostState,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val portalUrl = "${BuildConfig.SERVER_BASE_URL.trimEnd('/')}/account/github"

    fun openPortal() {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(portalUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }.onFailure {
            scope.launch { snackbar.showSnackbar("无法打开浏览器，请访问 $portalUrl") }
        }
    }

    Card(modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("GitHub 账号与权限", fontWeight = FontWeight.Bold)
            Text(
                "GitHub 授权已经移到 FDEX 用户 Web 中心。使用当前 FDEX 账号登录网页后，由你本人连接自己的 GitHub、选择仓库并配置 Push / PR / 构建联网权限。",
                color = Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                "Android 不再要求输入 GitHub Token，也不保存 GitHub access token / refresh token。",
                color = Emerald,
                style = MaterialTheme.typography.bodySmall,
            )
            Button(onClick = ::openPortal, modifier = Modifier.fillMaxWidth()) {
                Text("打开 FDEX GitHub 用户中心")
            }
            OutlinedButton(
                onClick = {
                    onRefresh()
                    scope.launch { snackbar.showSnackbar("正在刷新当前账号的 Agent 项目") }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("我已在网页配置，刷新项目")
            }
            Text(portalUrl, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}
