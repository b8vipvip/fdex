package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.CentralSessionStore
import com.b8vipvip.fdex.network.CentralAuthApi
import com.b8vipvip.fdex.network.CentralAuthResult
import kotlinx.coroutines.launch

@Composable
internal fun LoginScreen(onLogin: () -> Unit, onRegister: () -> Unit) {
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val scope = rememberCoroutineScope()
    var email by remember { mutableStateOf(sessions.email()) }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var forgotMode by remember { mutableStateOf(false) }
    var resetRequested by remember { mutableStateOf(false) }
    var resetCode by remember { mutableStateOf("") }
    var newPassword by remember { mutableStateOf("") }
    var confirmPassword by remember { mutableStateOf("") }
    var resetMessage by remember { mutableStateOf("") }

    if (forgotMode) {
        AuthFrame("找回 FDEX 密码", "通过登录邮箱验证码重置中心账号密码") {
            OutlinedTextField(email, { email = it }, label = { Text("登录邮箱") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
            if (!resetRequested) {
                Button(
                    enabled = !busy && email.isNotBlank(),
                    onClick = {
                        busy = true; error = ""; resetMessage = ""
                        scope.launch {
                            when (val result = CentralAuthApi.requestPasswordReset(email)) {
                                is CentralAuthResult.Success -> { resetRequested = true; resetMessage = result.value }
                                is CentralAuthResult.Failure -> error = result.message
                            }
                            busy = false
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (busy) "发送中…" else "发送邮箱验证码") }
            } else {
                if (resetMessage.isNotBlank()) Text(resetMessage, color = Emerald)
                OutlinedTextField(resetCode, { resetCode = it.trim() }, label = { Text("邮件验证码") }, modifier = Modifier.fillMaxWidth(), enabled = !busy, singleLine = true)
                OutlinedTextField(newPassword, { newPassword = it }, label = { Text("新密码（至少 8 位）") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
                OutlinedTextField(confirmPassword, { confirmPassword = it }, label = { Text("再次输入新密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
                Button(
                    enabled = !busy && resetCode.isNotBlank() && newPassword.length >= 8 && newPassword == confirmPassword,
                    onClick = {
                        busy = true; error = ""
                        scope.launch {
                            when (val result = CentralAuthApi.confirmPasswordReset(email, resetCode, newPassword)) {
                                is CentralAuthResult.Success -> {
                                    resetMessage = result.value
                                    password = ""; newPassword = ""; confirmPassword = ""; resetCode = ""
                                    forgotMode = false; resetRequested = false
                                }
                                is CentralAuthResult.Failure -> error = result.message
                            }
                            busy = false
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (busy) "重置中…" else "验证并重置密码") }
                OutlinedButton(
                    onClick = { resetRequested = false; resetCode = ""; error = "" },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !busy,
                ) { Text("重新发送验证码") }
            }
            if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
            TextButton(onClick = { forgotMode = false; error = "" }, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("返回登录") }
        }
        return
    }

    AuthFrame("登录 FDEX", "进入你的智体、知识与工作空间") {
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(password, { password = it }, label = { Text("密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
        if (resetMessage.isNotBlank()) Text(resetMessage, color = Emerald)
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(enabled = !busy && email.isNotBlank() && password.isNotBlank(), onClick = {
            busy = true; error = ""; resetMessage = ""
            scope.launch {
                when (val result = CentralAuthApi.login(email, password)) {
                    is CentralAuthResult.Success -> { sessions.save(result.value); password = ""; busy = false; onLogin() }
                    is CentralAuthResult.Failure -> { error = result.message; busy = false }
                }
            }
        }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "登录中…" else "登录") }
        TextButton(onClick = { forgotMode = true; error = "" }, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("忘记密码？用邮箱验证码找回") }
        TextButton(onClick = onRegister, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("还没有 FDEX 账号？注册") }
        Text("账号身份保存在 FDEX 中心服务器；本机数据按 user_id 使用独立数据库空间。", style = MaterialTheme.typography.bodySmall, color = Muted)
    }
}

@Composable
internal fun RegisterScreen(onDone: () -> Unit, onLogin: () -> Unit) {
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    AuthFrame("创建 FDEX 中心账号", "一个账号对应独立智体、知识、GitHub、项目、沙箱和本机数据空间") {
        OutlinedTextField(name, { name = it }, label = { Text("你的名字") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(password, { password = it }, label = { Text("密码（至少 8 位）") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(enabled = !busy && name.isNotBlank() && email.isNotBlank() && password.length >= 8, onClick = {
            busy = true; error = ""
            scope.launch {
                // The center API retains a legacy company argument for protocol compatibility; new
                // clients intentionally send it empty and never expose it as user identity.
                when (val result = CentralAuthApi.register(name, email, password, "")) {
                    is CentralAuthResult.Success -> { sessions.save(result.value); password = ""; busy = false; onDone() }
                    is CentralAuthResult.Failure -> { error = result.message; busy = false }
                }
            }
        }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "创建中…" else "创建并进入") }
        TextButton(onClick = onLogin, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("返回登录") }
    }
}

@Composable
private fun AuthFrame(title: String, subtitle: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.Center) {
        Text("FDEX", color = Emerald, fontWeight = FontWeight.Bold)
        Text(title, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
        Text(subtitle, color = Muted, modifier = Modifier.padding(top = 6.dp, bottom = 24.dp))
        Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp), content = content) }
    }
}
