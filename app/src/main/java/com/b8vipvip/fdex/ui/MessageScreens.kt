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
import androidx.compose.material3.Checkbox
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.EmployeeChatAccess
import com.b8vipvip.fdex.data.EmployeePermissions
import com.b8vipvip.fdex.data.KnowledgeStore
import com.b8vipvip.fdex.data.addAgent
import com.b8vipvip.fdex.data.isPrivateAssistant

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
    val agents = repo.employees().filter {
        query.isBlank() || it.name.contains(query, ignoreCase = true) || it.rolePrompt.contains(query, ignoreCase = true)
    }
    val groups = repo.groups().filter {
        query.isBlank() || "${it.name}${it.description}".contains(query, ignoreCase = true)
    }
    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            OutlinedTextField(
                query,
                { query = it },
                label = { Text("搜索智体、群名或内容") },
                modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                singleLine = true,
            )
        }
        if (groups.isNotEmpty()) item { SectionTitle("工作群") }
        items(groups, key = { "g${it.id}" }) { group ->
            ConversationRow(
                "👥",
                group.name,
                "${group.memberIds.size} 个智体",
                repo.groupMessages(group.id).lastOrNull()?.content ?: "工作群已创建",
            ) { onGroup(group.id) }
        }
        if (agents.isNotEmpty()) item { SectionTitle("智体") }
        items(agents, key = { "e${it.id}" }) { agent ->
            ConversationRow(
                employeeEmoji(agent),
                agent.name,
                if (agent.rolePrompt.isBlank()) "通用智体" else "已定义身份",
                repo.messages(agent.id).lastOrNull()?.content ?: "开始与智体沟通",
            ) { onEmployee(agent.id) }
        }
        item {
            OutlinedButton(onClick = onAddEmployee, modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text("＋ 添加智体")
            }
        }
    }
}

@Composable
internal fun EmployeeManageScreen(
    repo: AppRepository,
    revision: Int,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onChat: (Long) -> Unit,
    onChanged: () -> Unit,
) {
    revision.hashCode()
    val context = LocalContext.current
    val knowledgeStore = remember { KnowledgeStore(context) }
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Button(onClick = onAdd, modifier = Modifier.fillMaxWidth()) { Text("添加智体") }
        }
        item {
            Text(
                "智体是用户自定义的通用 AI 身份，可以是语文老师、数学老师、体育老师、学习伙伴、生活助手或 Coding Agent。",
                color = Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
        items(repo.employees(activeOnly = false), key = { it.id }) { agent ->
            val permissions = knowledgeStore.permissionsFor(agent.id)
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Avatar(employeeEmoji(agent))
                        Column(Modifier.weight(1f).padding(start = 10.dp).clickable { onChat(agent.id) }) {
                            Text(agent.name, fontWeight = FontWeight.SemiBold)
                            Text(
                                if (agent.rolePrompt.isBlank()) "通用智体 · 身份定义留空" else "身份定义已设置",
                                color = Muted,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(permissionSummary(permissions), color = Muted, style = MaterialTheme.typography.bodySmall)
                            if (!agent.active) Text("已停用", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End) {
                        TextButton(onClick = { onEdit(agent.id) }) { Text("编辑") }
                        if (agent.active && !agent.materialManager && !agent.isPrivateAssistant()) {
                            TextButton(onClick = { repo.resignEmployee(agent.id); onChanged() }) { Text("停用") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
internal fun AddEmployeeScreen(
    repo: AppRepository,
    snackbar: SnackbarHostState,
    onDone: () -> Unit,
) {
    EmployeeEditor(repo = repo, initial = null, snackbar = snackbar, onDone = onDone)
}

@Composable
internal fun EditEmployeeScreen(
    repo: AppRepository,
    employeeId: Long,
    snackbar: SnackbarHostState,
    onDone: () -> Unit,
) {
    val agent = repo.employee(employeeId)
    if (agent == null) {
        Column(Modifier.fillMaxSize().padding(16.dp)) { Text("智体不存在或已被删除") }
        return
    }
    EmployeeEditor(repo = repo, initial = agent, snackbar = snackbar, onDone = onDone)
}

@Composable
private fun EmployeeEditor(
    repo: AppRepository,
    initial: Employee?,
    snackbar: SnackbarHostState,
    onDone: () -> Unit,
) {
    snackbar.hashCode()
    val context = LocalContext.current
    val knowledgeStore = remember { KnowledgeStore(context) }
    val initialPermissions = remember(initial?.id) {
        initial?.let { knowledgeStore.permissionsFor(it.id) } ?: EmployeePermissions()
    }
    var name by remember(initial?.id) { mutableStateOf(initial?.name.orEmpty()) }
    var prompt by remember(initial?.id) { mutableStateOf(initial?.rolePrompt.orEmpty()) }
    var knowledgeRead by remember(initial?.id) { mutableStateOf(initialPermissions.knowledgeRead) }
    var knowledgeWrite by remember(initial?.id) { mutableStateOf(initialPermissions.knowledgeWrite) }
    var chatAccessMode by remember(initial?.id) { mutableStateOf(initialPermissions.chatAccessMode) }
    val readableEmployeeIds = remember(initial?.id) {
        mutableStateListOf<Long>().apply { addAll(initialPermissions.readableEmployeeIds) }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            if (initial == null) "创建智体" else "编辑智体",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )
        Text(
            if (initial == null) {
                "只需要填写身份定义提示词，也可以留空。留空时会创建通用智体，并自动分配“智体 1、智体 2…”作为名称。"
            } else {
                "可以修改显示名称、身份定义提示词和知识/聊天读取权限。身份定义提示词留空时按通用智体工作。"
            },
            color = Muted,
            style = MaterialTheme.typography.bodySmall,
        )

        if (initial != null) {
            OutlinedTextField(
                value = name,
                onValueChange = { name = it },
                label = { Text("显示名称（可选）") },
                placeholder = { Text("例如：语文老师、数学老师、小明") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
        }

        OutlinedTextField(
            value = prompt,
            onValueChange = { prompt = it },
            label = { Text("身份定义提示词（可选）") },
            placeholder = {
                Text("例如：你是我的语文老师，擅长阅读理解和作文教学。根据我的水平循序渐进讲解，并在必要时出题检查掌握情况。")
            },
            minLines = 8,
            maxLines = 18,
            modifier = Modifier.fillMaxWidth(),
        )

        if (initial != null) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("权限设置", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                    Text(
                        "权限决定这个智体回答时能读取哪些资料。关闭共享写入不会影响正常聊天和本机归档。",
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    PermissionCheckRow(
                        title = "读取知识库",
                        description = "允许检索已标记为可共享召回的知识摘要和关键词。",
                        checked = knowledgeRead,
                    ) { knowledgeRead = it }
                    PermissionCheckRow(
                        title = "写入共享知识",
                        description = "允许该智体产生的知识摘要被其他有读取权限的智体召回。",
                        checked = knowledgeWrite,
                    ) { knowledgeWrite = it }

                    Text("聊天记录读取范围", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                    listOf(
                        EmployeeChatAccess.NONE to "不读取聊天记录",
                        EmployeeChatAccess.SELF to "仅读取自己的历史聊天",
                        EmployeeChatAccess.ALL to "读取所有智体聊天记录",
                        EmployeeChatAccess.SELECTED to "只读取指定智体聊天记录",
                    ).forEach { (mode, label) ->
                        OutlinedButton(onClick = { chatAccessMode = mode }, modifier = Modifier.fillMaxWidth()) {
                            Text(if (chatAccessMode == mode) "✓ $label" else label)
                        }
                    }

                    if (chatAccessMode == EmployeeChatAccess.SELECTED) {
                        Text("指定可读取智体", color = Muted, style = MaterialTheme.typography.bodySmall)
                        val candidates = repo.employees(activeOnly = false).filter { it.id != initial.id }
                        if (candidates.isEmpty()) {
                            Text("当前没有其他智体可选择", color = Muted)
                        } else {
                            candidates.forEach { agent ->
                                Row(
                                    Modifier.fillMaxWidth().clickable {
                                        if (readableEmployeeIds.contains(agent.id)) readableEmployeeIds.remove(agent.id)
                                        else readableEmployeeIds.add(agent.id)
                                    },
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Checkbox(
                                        checked = readableEmployeeIds.contains(agent.id),
                                        onCheckedChange = { checked ->
                                            if (checked) {
                                                if (!readableEmployeeIds.contains(agent.id)) readableEmployeeIds.add(agent.id)
                                            } else readableEmployeeIds.remove(agent.id)
                                        },
                                    )
                                    Text(agent.name)
                                }
                            }
                        }
                    }
                }
            }
        }

        Button(
            enabled = canSaveEmployeeEditor(
                isEditing = initial != null,
                name = name,
                department = "",
                position = "",
                prompt = prompt,
                generating = false,
            ),
            onClick = {
                val agentId = if (initial == null) {
                    repo.addAgent(prompt).id
                } else {
                    repo.updateEmployee(
                        initial.copy(
                            name = name.trim().ifBlank { initial.name },
                            department = "",
                            position = "",
                            rolePrompt = prompt.trim(),
                            industry = "",
                        ),
                    )
                    initial.id
                }
                knowledgeStore.savePermissions(
                    agentId,
                    if (initial == null) EmployeePermissions() else EmployeePermissions(
                        knowledgeRead = knowledgeRead,
                        knowledgeWrite = knowledgeWrite,
                        chatAccessMode = chatAccessMode,
                        readableEmployeeIds = readableEmployeeIds.toList(),
                    ),
                )
                onDone()
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (initial == null) "创建智体" else "保存修改") }
    }
}

@Composable
private fun PermissionCheckRow(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().clickable { onCheckedChange(!checked) },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(checked = checked, onCheckedChange = onCheckedChange)
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(description, color = Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

private fun permissionSummary(permissions: EmployeePermissions): String {
    val knowledge = buildList {
        if (permissions.knowledgeRead) add("知识读")
        if (permissions.knowledgeWrite) add("知识写")
    }.joinToString("/").ifBlank { "知识无权限" }
    val chat = when (permissions.chatAccessMode) {
        EmployeeChatAccess.NONE -> "不读聊天"
        EmployeeChatAccess.ALL -> "全部智体聊天"
        EmployeeChatAccess.SELECTED -> "指定智体聊天(${permissions.readableEmployeeIds.size})"
        else -> "仅自己聊天"
    }
    return "权限：$knowledge · $chat"
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
        OutlinedTextField(description, { description = it }, label = { Text("群用途 / 说明") }, minLines = 3, modifier = Modifier.fillMaxWidth())
        Text("选择智体", fontWeight = FontWeight.SemiBold)
        repo.employees().forEach { agent ->
            Row(
                Modifier.fillMaxWidth().clickable {
                    if (selected.contains(agent.id)) selected.remove(agent.id) else selected.add(agent.id)
                },
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Checkbox(
                    checked = selected.contains(agent.id),
                    onCheckedChange = { checked -> if (checked) selected.add(agent.id) else selected.remove(agent.id) },
                )
                Text(agent.name)
            }
        }
        ToggleRow("自动协作模式", auto) { auto = it }
        Button(
            enabled = name.isNotBlank(),
            onClick = {
                val ids = if (selected.isEmpty()) repo.employees().map { it.id } else selected.toList()
                val group = repo.createGroup(name, description, null, ids, auto)
                repo.addGroupMessage(group.id, "system", "", "工作群已创建，可以 @智体 或直接安排协作任务。")
                onDone(group.id)
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("创建工作群") }
    }
}
