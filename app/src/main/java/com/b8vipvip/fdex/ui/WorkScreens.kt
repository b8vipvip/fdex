package com.b8vipvip.fdex.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.AppRepository
import com.b8vipvip.fdex.data.Project
import com.b8vipvip.fdex.data.ProjectAsset
import com.b8vipvip.fdex.network.AiGatewayResult
import com.b8vipvip.fdex.network.ChatAttachment
import com.b8vipvip.fdex.network.ClientAiApi
import com.b8vipvip.fdex.network.chatAttachmentKindFor
import com.b8vipvip.fdex.network.encodeChatContent
import kotlinx.coroutines.launch
import java.time.Instant

@Composable
internal fun WorkScreen(repo: AppRepository, revision: Int, onOpen: (Long) -> Unit, onNew: () -> Unit) {
    revision.hashCode()
    val projects = repo.projects()
    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Text("工作区", color = Blue, fontWeight = FontWeight.SemiBold)
            Text("让 AI 帮你分析资料并生成可执行方案", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Text("把业务问题、任务、流程或想法持续沉淀成一项工作。", color = Muted, modifier = Modifier.padding(top = 4.dp, bottom = 8.dp))
        }
        if (projects.isEmpty()) {
            item { EmptyCard("🚀", "你还没有工作", "先创建一个工作，告诉 AI 你想解决什么业务问题。", "创建第一个工作", onNew) }
        }
        items(projects, key = { it.id }) { project -> ProjectCard(project) { onOpen(project.id) } }
    }
}

@Composable
private fun ProjectCard(project: Project, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().then(Modifier)) {
        Column(Modifier.padding(16.dp)) {
            Row {
                Text(project.title, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
                StatusPill(project.status)
            }
            Text(project.description.ifBlank { "暂无工作描述" }, color = Muted, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 8.dp))
            Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("完整度 ${project.requirementScore}%", color = Muted)
                Text(storageLabel(project.storageMode), color = Muted)
                Button(onClick = onClick) { Text("打开") }
            }
        }
    }
}

@Composable
internal fun NewProjectScreen(repo: AppRepository, onDone: (Long) -> Unit) {
    var title by remember { mutableStateOf("") }
    var description by remember { mutableStateOf("") }
    var level by remember { mutableStateOf("business") }
    var storage by remember { mutableStateOf("hybrid") }
    var retention by remember { mutableStateOf("keep_forever") }
    var allowAi by remember { mutableStateOf(true) }
    var desensitize by remember { mutableStateOf(true) }
    var auto by remember { mutableStateOf(repo.profile().autoCompanyMode) }
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("新增工作", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text("把一个业务问题、任务、流程或想法创建成工作，后续持续补充资料并让 AI 生成方案。", color = Muted)
        OutlinedTextField(title, { title = it }, label = { Text("工作名称") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(description, { description = it }, label = { Text("用大白话描述需求") }, minLines = 5, modifier = Modifier.fillMaxWidth())
        SelectorCard(
            "专业程度",
            listOf("beginner" to "完全小白", "business" to "懂业务不懂技术", "product" to "产品/项目经理", "developer" to "技术人员", "auto" to "AI 自动判断"),
            level,
        ) { level = it }
        SelectorCard(
            "数据存储方式",
            listOf("hybrid" to "混合模式（推荐）", "local_only" to "本地模式", "cloud" to "云端模式", "temporary" to "临时分析模式"),
            storage,
        ) { storage = it }
        SelectorCard(
            "原始文件保留时间",
            listOf("keep_forever" to "长期保留", "delete_after_analysis" to "分析后删除", "delete_after_1_day" to "1 天后删除", "delete_after_7_days" to "7 天后删除", "delete_after_30_days" to "30 天后删除"),
            retention,
        ) { retention = it }
        ToggleRow("允许第三方 AI 分析", allowAi) { allowAi = it }
        ToggleRow("自动脱敏后再分析", desensitize) { desensitize = it }
        ToggleRow("创建后启动公司自动运营", auto) { auto = it }
        Button(
            enabled = title.isNotBlank(),
            onClick = {
                onDone(repo.createProject(title, description, level, storage, retention, allowAi, desensitize, auto).id)
            },
            modifier = Modifier.fillMaxWidth(),
        ) { Text("创建工作") }
    }
}

@Composable
internal fun ProjectDetailScreen(
    repo: AppRepository,
    projectId: Long,
    revision: Int,
    onChanged: () -> Unit,
    onGroup: (Long) -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val project = repo.project(projectId) ?: return
    var note by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            runCatching { context.contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
            val meta = queryDocument(context, uri)
            repo.addAsset(projectId, meta.first, uri, meta.second, meta.third)
            onChanged()
        }
    }

    LazyColumn(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Card {
                Column(Modifier.padding(16.dp)) {
                    Row {
                        Text(project.title, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                        StatusPill(project.status)
                    }
                    Text(project.description, color = Muted, modifier = Modifier.padding(top = 8.dp))
                    Row(Modifier.fillMaxWidth().padding(top = 14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                        Metric("完整度", "${project.requirementScore}%")
                        Metric("隐私模式", storageLabel(project.storageMode))
                        Metric("资料", "${repo.assets(projectId).size} 份")
                    }
                }
            }
        }
        item {
            NextActionCard(project, repo.reports(projectId).isNotEmpty(), busy) {
                busy = true
                scope.launch {
                    generateProjectReport(repo, project, snackbar)
                    busy = false
                    onChanged()
                }
            }
        }
        item {
            StepCard(1, "补充需求", "把新想法、目标或限制继续告诉 AI。") {
                OutlinedTextField(note, { note = it }, label = { Text("补充内容") }, modifier = Modifier.fillMaxWidth())
                Button(
                    enabled = note.isNotBlank(),
                    onClick = { repo.addNote(projectId, note); note = ""; onChanged() },
                    modifier = Modifier.padding(top = 8.dp),
                ) { Text("记录") }
                repo.notes(projectId).forEach { Text("• ${it.content}", color = Muted, modifier = Modifier.padding(top = 6.dp)) }
            }
        }
        item {
            StepCard(2, "上传并分析资料", "原文件保留在当前设备；点击分析后临时读取。PDF/Word/Excel/PPT/文本由 FDEX 服务端内存提取正文，视频在手机端抽取代表画面进入视觉模型。") {
                Button(onClick = { launcher.launch(arrayOf("*/*")) }) { Text("选择文件") }
                repo.assets(projectId).forEach { asset ->
                    AssetRow(
                        asset,
                        busy = busy,
                        onPrivacy = { decision -> repo.updateAsset(asset.copy(privacyDecision = decision)); onChanged() },
                        onAnalyze = {
                            busy = true
                            scope.launch {
                                analyzeAsset(repo, project, asset, context, snackbar)
                                busy = false
                                onChanged()
                            }
                        },
                    )
                }
            }
        }
        item {
            StepCard(3, "AI 分析结果", "查看每份资料提取出的关键需求、风险和建议。") {
                val analyzed = repo.assets(projectId).filter { it.analysis.isNotBlank() }
                if (analyzed.isEmpty()) Text("暂无分析结果", color = Muted)
                analyzed.forEach {
                    Text(it.name, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp))
                    Text(it.analysis, color = Muted, modifier = Modifier.padding(top = 4.dp))
                }
            }
        }
        item {
            StepCard(4, "方案文档", "查看 AI 为当前工作生成的正式方案。") {
                Button(
                    enabled = !busy,
                    onClick = {
                        busy = true
                        scope.launch {
                            generateProjectReport(repo, project, snackbar)
                            busy = false
                            onChanged()
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(if (busy) "AI 正在生成…" else "让 AI 生成完整方案") }
                repo.reports(projectId).forEach { ReportCard(it) }
            }
        }
        if (project.autoOperation) {
            item {
                val group = repo.groups().firstOrNull { it.projectId == project.id }
                if (group != null) {
                    Card {
                        Column(Modifier.padding(16.dp)) {
                            Text("公司自动运营", color = Emerald, fontWeight = FontWeight.Bold)
                            Text("工作群已创建，AI 团队可以在群里协同推进。", color = Muted)
                            Button(onClick = { onGroup(group.id) }, modifier = Modifier.padding(top = 8.dp)) { Text("进入工作群") }
                        }
                    }
                }
            }
        }
    }
}

private suspend fun analyzeAsset(
    repo: AppRepository,
    project: Project,
    asset: ProjectAsset,
    context: Context,
    snackbar: SnackbarHostState,
) {
    val system = "你是企业资料分析助手。请基于实际读取到的资料正文或画面，输出关键事实、与工作相关的需求、风险、下一步建议。无法读取的部分必须明确说明，禁止只凭文件名猜内容。"
    val prompt = "工作：${project.title}\n需求：${project.description}\n请读取并分析这份真实附件：${asset.name}。"
    val content = encodeChatContent(
        prompt,
        listOf(
            ChatAttachment(
                name = asset.name,
                uri = asset.uri,
                mimeType = asset.mimeType,
                size = asset.size,
                kind = chatAttachmentKindFor(asset.name, asset.mimeType),
            ),
        ),
    )
    when (val result = ClientAiApi.ask(system, content, context = context)) {
        is AiGatewayResult.Success -> {
            repo.updateAsset(asset.copy(status = "analyzed", analysis = result.content))
            repo.updateProject(project.copy(status = "analyzed", updatedAt = Instant.now().toString()))
        }
        is AiGatewayResult.Failure -> snackbar.showSnackbar(result.message)
    }
}

private suspend fun generateProjectReport(repo: AppRepository, project: Project, snackbar: SnackbarHostState) {
    val notes = repo.notes(project.id).joinToString("\n") { it.content }
    val analyses = repo.assets(project.id).filter { it.analysis.isNotBlank() }.joinToString("\n\n") { "${it.name}: ${it.analysis}" }
    val prompt = "工作名称：${project.title}\n原始需求：${project.description}\n补充信息：$notes\n资料分析：$analyses"
    when (val result = ClientAiApi.ask(
        "你是企业项目顾问。生成结构清晰的中文执行方案，包含目标、现状判断、关键问题、行动步骤、优先级、风险与检查指标。",
        prompt,
        1800,
    )) {
        is AiGatewayResult.Success -> repo.addReport(project.id, "${project.title} · AI 执行方案", result.content)
        is AiGatewayResult.Failure -> snackbar.showSnackbar(result.message)
    }
}

private fun queryDocument(context: Context, uri: Uri): Triple<String, Long, String> {
    var name = "资料"
    var size = 0L
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) {
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
            if (nameIndex >= 0) name = cursor.getString(nameIndex) ?: name
            if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = cursor.getLong(sizeIndex)
        }
    }
    return Triple(name, size, context.contentResolver.getType(uri) ?: "application/octet-stream")
}
