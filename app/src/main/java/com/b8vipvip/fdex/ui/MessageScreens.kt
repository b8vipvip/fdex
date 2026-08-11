package com.b8vipvip.fdex.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.isPrivateAssistant
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.ClientAiApi
import kotlinx.coroutines.launch

@Composable
internal fun MessagesScreen(
    repo: AppRepository,
    revision: Int,
    onEmployee: (Long) -> Unit,
    onGroup: (Long) -> Unit,
    onAddEmployee: () -> Unit,
) {
    revision.hashCode()
    var query by remember { mutableStateOf("") }
    val employees = repo.employees().filter {
        query.isBlank() || "${it.name}${it.position}${it.department}".contains(query, ignoreCase = true)
    }
    val groups = repo.groups().filter {
        query.isBlank() || "${it.name}${it.description}".contains(query, ignoreCase = true)
    }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            OutlinedTextField(
                query,
                { query = it },
                label = { Text("搜索员工、群名或工作") },
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                singleLine = true,
            )
        }
        if (groups.isNotEmpty()) item { SectionTitle("工作群") }
        items(groups, key = { "g${it.id}" }) { group ->
            ConversationRow(
                "👥",
                group.name,
                "${group.memberIds.size} 名成员",
                repo.groupMessages(group.id).lastOrNull()?.content ?: "工作群已创建",
            ) { onGroup(group.id) }
        }
        if (employees.isNotEmpty()) item { SectionTitle("AI 员工") }
        items(employees, key = { "e${it.id}" }) { employee ->
            ConversationRow(
                employeeEmoji(employee),
                employee.name,
                employee.position,
                repo.messages(employee.id).lastOrNull()?.content ?: "开始与 AI 员工沟通",
            ) { onEmployee(employee.id) }
        }
        item {
            OutlinedButton(onClick = onAddEmployee, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text("＋ 添加 AI 员工")
            }
        }
    }
}

@Composable
internal fun EmployeeChatScreen(
    repo: AppRepository,
    employeeId: Long,
    revision: Int,
    onChanged: () -> Unit,
    onOpenManage: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val employee = repo.employee(employeeId) ?: return
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(employeeEmoji(employee))
            Column(Modifier.weight(1f).padding(start = 10.dp)) {
                Text("${employee.name} · ${employee.position}", fontWeight = FontWeight.SemiBold)
                Text(employee.department, color = Muted)
            }
            Box {
                TextButton(onClick = { menu = true }) { Text("•••") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(text = { Text("员工管理") }, onClick = { menu = false; onOpenManage() })
                    DropdownMenuItem(
                        text = { Text("清空聊天记录") },
                        onClick = { repo.clearMessages(employeeId); menu = false; onChanged() },
                    )
                }
            }
        }
        LazyColumn(Modifier.weight(1f).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(repo.messages(employeeId), key = { it.id }) { message -> MessageBubble(message, employee) }
            if (busy) item { Text("${employee.name} 正在思考…", color = Muted, modifier = Modifier.padding(8.dp)) }
        }
        Row(Modifier.fillMaxWidth().background(Color.White).padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                text,
                { text = it },
                placeholder = { Text("给员工安排任务…") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                enabled = text.isNotBlank() && !busy,
                onClick = {
                    val prompt = text.trim()
                    text = ""
                    repo.addMessage(employeeId, "user", prompt)
                    onChanged()
                    busy = true
                    scope.launch {
                        val system = if (employee.isPrivateAssistant()) null else
                            "你是 FDEX AI 虚拟公司的员工：${employee.name}，职位：${employee.position}，部门：${employee.department}。${employee.rolePrompt}。像真实同事一样简洁、主动、可执行地回答。"
                        when (val result = ClientAiApi.ask(system, prompt)) {
                            is AiGatewayResult.Success -> repo.addMessage(employeeId, "employee", result.content)
                            is AiGatewayResult.Failure -> {
                                repo.addMessage(employeeId, "employee", "暂时无法完成请求：${result.message}")
                                snackbar.showSnackbar(result.message)
                            }
                        }
                        busy = false
                        onChanged()
                    }
                },
            ) { Text("发送") }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage, employee: Employee) {
    val isUser = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        if (!isUser) {
            Avatar(employeeEmoji(employee), 36)
            Spacer(Modifier.width(8.dp))
        }
        Column(
            horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
            modifier = Modifier.fillMaxWidth(.78f),
        ) {
            Text(if (isUser) "我" else employee.name, color = Muted)
            Card {
                Text(
                    message.content,
                    modifier = Modifier.padding(12.dp),
                    color = if (isUser) Emerald else MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}

@Composable
internal fun EmployeeManageScreen(
    repo: AppRepository,
    revision: Int,
    onAdd: () -> Unit,
    onChat: (Long) -> Unit,
    onChanged: () -> Unit,
) {
    revision.hashCode()
    var industry by remember { mutableStateOf(repo.profile().industry) }
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.fillMaxWidth()) {
                Button(onClick = onAdd, modifier = Modifier.weight(1f)) { Text("添加员工") }
                Spacer(Modifier.width(8.dp))
                OutlinedButton(
                    onClick = { repo.bulkAddEmployees(industry); onChanged() },
                    modifier = Modifier.weight(1f),
                ) { Text("按行业批量添加") }
            }
        }
        item {
            OutlinedTextField(industry, { industry = it }, label = { Text("批量添加行业") }, modifier = Modifier.fillMaxWidth())
        }
        items(repo.employees(), key = { it.id }) { employee ->
            Card(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Avatar(employeeEmoji(employee))
                    Column(Modifier.weight(1f).padding(start = 10.dp).clickable { onChat(employee.id) }) {
                        Text("${employee.name} · ${employee.position}", fontWeight = FontWeight.SemiBold)
                        val systemLabel = when {
                            employee.isPrivateAssistant() -> " · 内置私人助理"
                            employee.materialManager -> " · 系统资料员"
                            else -> ""
                        }
                        Text("${employee.department}$systemLabel", color = Muted)
                    }
                    if (!employee.materialManager && !employee.isPrivateAssistant()) {
                        TextButton(onClick = { repo.resignEmployee(employee.id); onChanged() }) { Text("离职") }
                    }
                }
            }
        }
    }
}

@Composable
internal fun AddEmployeeScreen(repo: AppRepository, onDone: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var department by remember { mutableStateOf("") }
    var position by remember { mutableStateOf("") }
    var prompt by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("创建 AI 员工", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        OutlinedTextField(name, { name = it }, label = { Text("员工名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(department, { department = it }, label = { Text("部门") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(position, { position = it }, label = { Text("职位") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(prompt, { prompt = it }, label = { Text("角色职责 / Prompt") }, minLines = 4, modifier = Modifier.fillMaxWidth())
        Button(
            enabled = name.isNotBlank() && position.isNotBlank(),
            onClick = {
                repo.addEmployee(
                    name,
                    department,
                    position,
                    prompt.ifBlank { "你是公司的$position，请从岗位角度给出专业、可执行的协助。" },
                    repo.profile().industry,
                )
                onDone()
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("保存员工") }
    }
}

@Composable
internal fun NewGroupScreen(repo: AppRepository, onDone: (Long) -> Unit) {
    var name by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var auto by remember { mutableStateOf(false) }
    val selected = remember { mutableStateListOf<Long>() }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("创建工作群", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        OutlinedTextField(name, { name = it }, label = { Text("群名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(description, { description = it }, label = { Text("群用途 / 工作说明") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        Text("选择成员", fontWeight = FontWeight.SemiBold)
        repo.employees().forEach { employee ->
            Row(
                Modifier.fillMaxWidth().clickable {
                    if (selected.contains(employee.id)) selected.remove(employee.id) else selected.add(employee.id)
                },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                androidx.compose.material3.Checkbox(
                    checked = selected.contains(employee.id),
                    onCheckedChange = { checked -> if (checked) selected.add(employee.id) else selected.remove(employee.id) },
                )
                Text("${employee.name} · ${employee.position}")
            }
        }
        ToggleRow("自动运营模式", auto) { auto = it }
        Button(
            enabled = name.isNotBlank(),
            onClick = {
                val ids = if (selected.isEmpty()) repo.employees().map { it.id } else selected.toList()
                val group = repo.createGroup(name, description, null, ids, auto)
                repo.addGroupMessage(group.id, "system", "", "工作群已创建，可以 @员工 或直接安排团队任务。")
                onDone(group.id)
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("创建工作群") }
    }
}

@Composable
internal fun GroupChatScreen(
    repo: AppRepository,
    groupId: Long,
    revision: Int,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val group = repo.group(groupId) ?: return
    val members = group.memberIds.mapNotNull { repo.employee(it) }
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize()) {
        Card(Modifier.fillMaxWidth().padding(10.dp)) {
            Column(Modifier.padding(12.dp)) {
                Text("👥 ${group.name}", fontWeight = FontWeight.Bold)
                Text("${members.size} 名成员 · ${if (group.autoMode) "自动运营" else "人工指挥"}", color = Emerald)
                Text(group.description, color = Muted)
            }
        }
        LazyColumn(Modifier.weight(1f).padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(repo.groupMessages(groupId), key = { it.id }) { GroupBubble(it) }
            if (busy) item { Text("团队正在处理…", color = Muted) }
        }
        Row(Modifier.fillMaxWidth().background(Color.White).padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                text,
                { text = it },
                placeholder = { Text("@员工 或安排团队任务…") },
                modifier = Modifier.weight(1f),
                maxLines = 4,
            )
            Spacer(Modifier.width(8.dp))
            Button(
                enabled = text.isNotBlank() && !busy,
                onClick = {
                    val prompt = text.trim()
                    text = ""
                    repo.addGroupMessage(groupId, "user", "我", prompt)
                    onChanged()
                    busy = true
                    scope.launch {
                        val target = members.firstOrNull { prompt.contains("@${it.name}") || prompt.contains(it.position) }
                            ?: members.firstOrNull()
                        val reply = if (target == null) {
                            "当前群里还没有 AI 员工。"
                        } else {
                            val system = if (target.isPrivateAssistant()) null else
                                "你是工作群里的${target.position} ${target.name}。${target.rolePrompt}。从团队协作角度简洁、可执行地回复。"
                            when (val result = ClientAiApi.ask(system, prompt)) {
                                is AiGatewayResult.Success -> result.content
                                is AiGatewayResult.Failure -> {
                                    snackbar.showSnackbar(result.message)
                                    "暂时无法完成：${result.message}"
                                }
                            }
                        }
                        repo.addGroupMessage(groupId, "employee", target?.name.orEmpty(), reply)
                        busy = false
                        onChanged()
                    }
                },
            ) { Text("发送") }
        }
    }
}
