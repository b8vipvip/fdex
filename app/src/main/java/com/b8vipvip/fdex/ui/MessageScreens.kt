package com.b8vipvip.fdex.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.runtime.rememberCoroutineScope
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
import com.b8vipvip.fdex.data.isPrivateAssistant
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.ClientAiApi
import kotlinx.coroutines.launch

private val RANDOM_EMPLOYEE_NAMES = listOf(
    "小安", "小岚", "小禾", "小程", "小林", "小夏", "小景", "小舟", "小宁", "小橙", "小北", "小满",
)

private val RANDOM_DEPARTMENTS = listOf(
    "运营中心", "市场中心", "销售中心", "产品中心", "客户成功中心", "财务中心",
    "人力资源中心", "数据中心", "研究中心", "项目中心", "内容中心", "技术中心",
)

private val RANDOM_POSITIONS = listOf(
    "运营专员", "项目经理", "市场策划", "销售顾问", "产品经理", "数据分析师",
    "行业研究员", "内容策划", "客户成功经理", "财务分析师", "招聘专员", "自动化工程师",
)

private fun randomEmployeeName(): String = RANDOM_EMPLOYEE_NAMES.random()
private fun randomDepartment(): String = RANDOM_DEPARTMENTS.random()
private fun randomPosition(): String = RANDOM_POSITIONS.random()

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
    var industry by remember { mutableStateOf(repo.profile().industry) }
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        item {
            Row(Modifier.fillMaxWidth()) {
                Button(onClick = onAdd, modifier = Modifier.weight(1f)) { Text("添加员工") }
                Spacer(Modifier.width(8.dp))
                OutlinedButton(
                    onClick = { repo.bulkAddEmployees(industry); onChanged() },
                    modifier = Modifier.weight(1f),
                ) { Text("批量添加基础员工") }
            }
        }
        item {
            OutlinedTextField(industry, { industry = it }, label = { Text("批量添加行业") }, modifier = Modifier.fillMaxWidth())
            Text(
                "批量添加只创建基础员工资料，不再内置 Prompt；创建后可逐个编辑或用 AI 生成提示词。",
                color = Muted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
        items(repo.employees(), key = { it.id }) { employee ->
            val permissions = knowledgeStore.permissionsFor(employee.id)
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Avatar(employeeEmoji(employee))
                        Column(Modifier.weight(1f).padding(start = 10.dp).clickable { onChat(employee.id) }) {
                            Text("${employee.name} · ${employee.position}", fontWeight = FontWeight.SemiBold)
                            Text(employee.department, color = Muted)
                            Text(
                                if (employee.rolePrompt.isBlank()) "Prompt 未设置" else "Prompt 已由客户端保存",
                                color = if (employee.rolePrompt.isBlank()) MaterialTheme.colorScheme.error else Emerald,
                                style = MaterialTheme.typography.bodySmall,
                            )
                            Text(permissionSummary(permissions), color = Muted, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.End) {
                        TextButton(onClick = { onEdit(employee.id) }) { Text("编辑") }
                        if (!employee.materialManager && !employee.isPrivateAssistant()) {
                            TextButton(onClick = { repo.resignEmployee(employee.id); onChanged() }) { Text("离职") }
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
    EmployeeEditor(
        repo = repo,
        initial = null,
        snackbar = snackbar,
        onDone = onDone,
    )
}

@Composable
internal fun EditEmployeeScreen(
    repo: AppRepository,
    employeeId: Long,
    snackbar: SnackbarHostState,
    onDone: () -> Unit,
) {
    val employee = repo.employee(employeeId)
    if (employee == null) {
        Column(Modifier.fillMaxSize().padding(16.dp)) { Text("员工不存在或已被删除") }
        return
    }
    EmployeeEditor(
        repo = repo,
        initial = employee,
        snackbar = snackbar,
        onDone = onDone,
    )
}

@Composable
private fun EmployeeEditor(
    repo: AppRepository,
    initial: Employee?,
    snackbar: SnackbarHostState,
    onDone: () -> Unit,
) {
    val context = LocalContext.current
    val knowledgeStore = remember { KnowledgeStore(context) }
    val initialPermissions = remember(initial?.id) {
        initial?.let { knowledgeStore.permissionsFor(it.id) } ?: EmployeePermissions()
    }
    val scope = rememberCoroutineScope()
    var name by remember(initial?.id) { mutableStateOf(initial?.name.orEmpty()) }
    var department by remember(initial?.id) { mutableStateOf(initial?.department ?: randomDepartment()) }
    var position by remember(initial?.id) { mutableStateOf(initial?.position ?: randomPosition()) }
    var idea by remember(initial?.id) { mutableStateOf("") }
    var prompt by remember(initial?.id) { mutableStateOf(initial?.rolePrompt.orEmpty()) }
    var generating by remember { mutableStateOf(false) }
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
            if (initial == null) "创建 AI 员工" else "编辑 AI 员工",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
        )
        Text(
            "员工角色 Prompt 保存在客户端员工资料中。普通聊天会在服务端按员工权限叠加 FDEX 企业知识、MemPalace 原始历史和 Letta 结构化记忆 system 层；实时语音仍以角色 Prompt 为会话基础。",
            color = Muted,
            style = MaterialTheme.typography.bodySmall,
        )

        RandomTextField(
            value = name,
            onValueChange = { name = it },
            label = "员工名称",
            onRandom = { name = randomEmployeeName() },
        )
        RandomTextField(
            value = department,
            onValueChange = { department = it },
            label = "部门",
            onRandom = { department = randomDepartment() },
        )
        RandomTextField(
            value = position,
            onValueChange = { position = it },
            label = "职位",
            onRandom = { position = randomPosition() },
        )

        OutlinedTextField(
            value = idea,
            onValueChange = { idea = it },
            label = { Text("一句话描述你想要的员工") },
            placeholder = { Text("例如：负责淘宝店运营，擅长活动策划和数据复盘，说话简洁直接") },
            modifier = Modifier.fillMaxWidth(),
            minLines = 2,
            maxLines = 4,
        )
        Button(
            enabled = idea.isNotBlank() && !generating,
            onClick = {
                generating = true
                scope.launch {
                    val request = buildPromptGenerationRequest(
                        description = idea.trim(),
                        name = name.trim(),
                        department = department.trim(),
                        position = position.trim(),
                    )
                    when (val result = ClientAiApi.ask(system = null, prompt = request, maxTokens = 1600)) {
                        is AiGatewayResult.Success -> {
                            val generated = result.content.trim()
                            if (generated.isNotBlank()) {
                                prompt = generated
                            } else {
                                snackbar.showSnackbar("AI 没有返回有效提示词")
                            }
                        }
                        is AiGatewayResult.Failure -> snackbar.showSnackbar("提示词生成失败：${result.message}")
                    }
                    generating = false
                }
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (generating) "正在生成提示词…" else "根据一句话 AI 生成提示词") }

        OutlinedTextField(
            value = prompt,
            onValueChange = { prompt = it },
            label = { Text("员工提示词（客户端保存）") },
            placeholder = { Text("可手动输入，也可以先用上方的一句话让 AI 生成，再自行修改") },
            minLines = 8,
            maxLines = 18,
            modifier = Modifier.fillMaxWidth(),
        )

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("权限设置", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                Text(
                    "权限决定这个员工回答时能读取哪些本地资料。所有聊天仍由系统自动归档；“写入知识库”决定该员工产生的知识摘要是否可共享给其他拥有读取权限的员工。",
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
                PermissionCheckRow(
                    title = "读取知识库",
                    description = "允许检索企业知识库中已标记为“员工可召回”的摘要和关键词。",
                    checked = knowledgeRead,
                ) { knowledgeRead = it }
                PermissionCheckRow(
                    title = "写入知识库",
                    description = "允许该员工后续聊天整理出的知识成为共享知识；关闭时聊天仍会归档，但只用于管理和显式聊天记录权限。",
                    checked = knowledgeWrite,
                ) { knowledgeWrite = it }

                Text("聊天记录读取范围", fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                listOf(
                    EmployeeChatAccess.NONE to "不读取聊天记录",
                    EmployeeChatAccess.SELF to "仅读取自己的历史聊天",
                    EmployeeChatAccess.ALL to "读取所有员工聊天记录",
                    EmployeeChatAccess.SELECTED to "只读取指定员工聊天记录",
                ).forEach { (mode, label) ->
                    OutlinedButton(
                        onClick = { chatAccessMode = mode },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (chatAccessMode == mode) "✓ $label" else label) }
                }

                if (chatAccessMode == EmployeeChatAccess.SELECTED) {
                    Text("指定可读取员工", color = Muted, style = MaterialTheme.typography.bodySmall)
                    val candidates = repo.employees(activeOnly = false).filter { it.id != initial?.id }
                    if (candidates.isEmpty()) {
                        Text("当前没有其他员工可选择", color = Muted)
                    } else {
                        candidates.forEach { employee ->
                            Row(
                                Modifier.fillMaxWidth().clickable {
                                    if (readableEmployeeIds.contains(employee.id)) {
                                        readableEmployeeIds.remove(employee.id)
                                    } else {
                                        readableEmployeeIds.add(employee.id)
                                    }
                                },
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Checkbox(
                                    checked = readableEmployeeIds.contains(employee.id),
                                    onCheckedChange = { checked ->
                                        if (checked) {
                                            if (!readableEmployeeIds.contains(employee.id)) readableEmployeeIds.add(employee.id)
                                        } else {
                                            readableEmployeeIds.remove(employee.id)
                                        }
                                    },
                                )
                                Column {
                                    Text("${employee.name} · ${employee.position}")
                                    Text(employee.department, color = Muted, style = MaterialTheme.typography.bodySmall)
                                }
                            }
                        }
                    }
                }
            }
        }

        Button(
            enabled = name.isNotBlank() && department.isNotBlank() && position.isNotBlank() && prompt.isNotBlank() && !generating,
            onClick = {
                val employeeId = if (initial == null) {
                    repo.addEmployee(
                        name = name,
                        department = department,
                        position = position,
                        prompt = prompt,
                        industry = repo.profile().industry,
                    ).id
                } else {
                    repo.updateEmployee(
                        initial.copy(
                            name = name.trim(),
                            department = department.trim(),
                            position = position.trim(),
                            rolePrompt = prompt.trim(),
                        ),
                    )
                    initial.id
                }
                knowledgeStore.savePermissions(
                    employeeId,
                    EmployeePermissions(
                        knowledgeRead = knowledgeRead,
                        knowledgeWrite = knowledgeWrite,
                        chatAccessMode = chatAccessMode,
                        readableEmployeeIds = readableEmployeeIds.toList(),
                    ),
                )
                onDone()
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (initial == null) "保存员工" else "保存修改") }
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
        if (permissions.knowledgeRead) add("知识库读")
        if (permissions.knowledgeWrite) add("知识库写")
    }.joinToString("/").ifBlank { "知识库无权限" }
    val chat = when (permissions.chatAccessMode) {
        EmployeeChatAccess.NONE -> "不读聊天"
        EmployeeChatAccess.ALL -> "全部聊天"
        EmployeeChatAccess.SELECTED -> "指定员工聊天(${permissions.readableEmployeeIds.size})"
        else -> "仅自己聊天"
    }
    return "权限：$knowledge · $chat"
}

@Composable
private fun RandomTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    onRandom: () -> Unit,
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            label = { Text(label) },
            modifier = Modifier.weight(1f),
            singleLine = true,
        )
        Spacer(Modifier.width(8.dp))
        OutlinedButton(onClick = onRandom) { Text("随机") }
    }
}

private fun buildPromptGenerationRequest(
    description: String,
    name: String,
    department: String,
    position: String,
): String = """
请根据下面用户对 AI 员工的一句话描述，生成一份可直接作为该员工 system prompt 使用的完整提示词。

员工名称：${name.ifBlank { "未命名" }}
部门：${department.ifBlank { "未指定" }}
职位：${position.ifBlank { "未指定" }}
用户描述：$description

要求：
- 只输出最终提示词正文，不要解释生成过程，不要使用 Markdown 代码块。
- 明确员工身份、核心职责、工作目标、工作边界、输出方式、沟通风格和需要主动追问的信息。
- 不要虚构订单、价格、库存、权限、公司内部事实或用户没有提供的数据。
- 提示词应适合长期保存到员工资料中，后续所有聊天直接使用。
- 中文自然、明确、可执行，避免空泛口号。
""".trimIndent()

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
                Checkbox(
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
