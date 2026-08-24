package com.b8vipvip.fdex.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
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
    AuthFrame("登录 FDEX", "登录中心账号，进入你的 AI 虚拟公司") {
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(password, { password = it }, label = { Text("密码") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(enabled = !busy && email.isNotBlank() && password.isNotBlank(), onClick = {
            busy = true; error = ""
            scope.launch {
                when (val result = CentralAuthApi.login(email, password)) {
                    is CentralAuthResult.Success -> { sessions.save(result.value); password = ""; busy = false; onLogin() }
                    is CentralAuthResult.Failure -> { error = result.message; busy = false }
                }
            }
        }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "登录中…" else "登录") }
        TextButton(onClick = onRegister, modifier = Modifier.fillMaxWidth(), enabled = !busy) { Text("还没有 FDEX 账号？注册") }
        Text("账号身份保存在 FDEX 中心服务器；本机业务数据按 user_id 使用独立数据库空间。", style = MaterialTheme.typography.bodySmall, color = Muted)
    }
}

@Composable
internal fun RegisterScreen(onDone: () -> Unit, onLogin: () -> Unit) {
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var company by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    AuthFrame("创建 FDEX 中心账号", "一个账号对应独立 GitHub、项目、沙箱和本机数据空间") {
        OutlinedTextField(name, { name = it }, label = { Text("你的名字") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(company, { company = it }, label = { Text("公司名称（可选）") }, modifier = Modifier.fillMaxWidth(), enabled = !busy)
        OutlinedTextField(password, { password = it }, label = { Text("密码（至少 8 位）") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(), enabled = !busy)
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(enabled = !busy && name.isNotBlank() && email.isNotBlank() && password.length >= 8, onClick = {
            busy = true; error = ""
            scope.launch {
                when (val result = CentralAuthApi.register(name, email, password, company)) {
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
