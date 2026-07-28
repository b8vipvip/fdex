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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository

@Composable
internal fun LoginScreen(repo: AppRepository, onLogin: () -> Unit, onRegister: () -> Unit) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    AuthFrame("登录 FDEX", "进入你的 AI 虚拟公司") {
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            password,
            { password = it },
            label = { Text("密码") },
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(
            onClick = {
                repo.login(email, password)
                    .onSuccess { onLogin() }
                    .onFailure { error = it.message.orEmpty() }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("登录") }
        TextButton(onClick = onRegister, modifier = Modifier.fillMaxWidth()) {
            Text(if (repo.hasAccount()) "重新创建本机账号" else "还没有账号？注册")
        }
        Text(
            "账号数据保存在本机；AI 请求只发送到你的 FDEX 服务端。",
            style = MaterialTheme.typography.bodySmall,
            color = Muted,
        )
    }
}

@Composable
internal fun RegisterScreen(repo: AppRepository, onDone: () -> Unit, onLogin: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var company by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    AuthFrame("创建 AI 虚拟公司", "注册后自动配备资料管理员、业务策划、行业研究员和执行经理") {
        OutlinedTextField(name, { name = it }, label = { Text("你的名字") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(email, { email = it }, label = { Text("邮箱") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(company, { company = it }, label = { Text("公司名称（可选）") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(
            password,
            { password = it },
            label = { Text("密码（至少 8 位）") },
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
        Button(
            onClick = {
                repo.register(name, email, password, company)
                    .onSuccess { onDone() }
                    .onFailure { error = it.message.orEmpty() }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("创建并进入") }
        TextButton(onClick = onLogin, modifier = Modifier.fillMaxWidth()) { Text("返回登录") }
    }
}

@Composable
private fun AuthFrame(title: String, subtitle: String, content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("FDEX", color = Emerald, fontWeight = FontWeight.Bold)
        Text(title, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
        Text(subtitle, color = Muted, modifier = Modifier.padding(top = 6.dp, bottom = 24.dp))
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp), content = content)
        }
    }
}
