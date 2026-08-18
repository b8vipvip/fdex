package com.b8vipvip.fdex.ui

import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.b8vipvip.fdex.data.KnowledgeEngine
import com.b8vipvip.fdex.data.KnowledgeEntry
import com.b8vipvip.fdex.data.KnowledgeRooms
import com.b8vipvip.fdex.data.KnowledgeStore
import com.b8vipvip.fdex.network.KnowledgeOrganizer
import kotlinx.coroutines.launch

private const val KNOWLEDGE_MODE_BROWSE = "browse"
private const val KNOWLEDGE_MODE_SEARCH = "search"
private const val KNOWLEDGE_MODE_WRITE = "write"

@Composable
internal fun KnowledgeScreen(
    repo: AppRepository,
    revision: Int,
    onOpenProject: (Long) -> Unit,
    onNewProject: () -> Unit,
    onChanged: () -> Unit,
    snackbar: SnackbarHostState,
) {
    revision.hashCode()
    val context = LocalContext.current
    val store = remember { KnowledgeStore(context) }
    val scope = rememberCoroutineScope()
    var mode by remember { mutableStateOf(KNOWLEDGE_MODE_BROWSE) }
    var query by remember { mutableStateOf("") }
    var semanticQuery by remember { mutableStateOf("") }
    var roomFilter by remember { mutableStateOf("all") }
    var manualTitle by remember { mutableStateOf("") }
    var manualContent by remember { mutableStateOf("") }
    var manualKeywords by remember { mutableStateOf("") }
    var manualRoom by remember { mutableStateOf(KnowledgeRooms.GENERAL) }
    var organizing by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        val imported = store.backfill(repo)
        val enriched = KnowledgeOrganizer.enrichPending(store, limit = 5)
        if (imported > 0 || enriched > 0) onChanged()
    }

    val entries = store.entries()
    val pending = entries.count { it.needsEnrichment }
    val casual = entries.count { it.room == KnowledgeRooms.CASUAL }
    val shared = entries.count { it.sharedForAgents }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Column(Modifier.padding(top = 10.dp)) {
                Text("企业知识库", color = Blue, fontWeight = FontWeight.SemiBold)
                Text(
                    "聊天自动沉淀、分类、摘要并生成检索关键词",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "原始聊天保存在本机 SQLite；知识条目按账户作用域隔离。日常问候和闲聊会单独归入“日常闲聊”。",
                    color = Muted,
                    modifier = Modifier.padding(top = 5.dp),
                )
            }
        }

        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                KnowledgeMetric("知识", "${entries.size} 条", Modifier.weight(1f))
                KnowledgeMetric("闲聊", "$casual 条", Modifier.weight(1f))
                KnowledgeMetric("员工可读", "$shared 条", Modifier.weight(1f))
            }
        }

        if (pending > 0) {
            item {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        Text("还有 $pending 条记录等待 AI 精细整理", fontWeight = FontWeight.SemiBold)
                        Text("本地摘要和关键词已经可检索；AI 整理会进一步优化分类、主题和关键词。", color = Muted)
                        Button(
                            enabled = !organizing,
                            onClick = {
                                organizing = true
                                scope.launch {
                                    val completed = KnowledgeOrganizer.enrichPending(store, limit = 10)
                                    organizing = false
                                    onChanged()
                                    snackbar.showSnackbar("本次完成 $completed 条知识整理")
                                }
                            },
                            modifier = Modifier.padding(top = 8.dp),
                        ) { Text(if (organizing) "正在整理…" else "继续 AI 整理") }
                    }
                }
            }
        }

        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                KnowledgeModeButton("浏览", mode == KNOWLEDGE_MODE_BROWSE, Modifier.weight(1f)) { mode = KNOWLEDGE_MODE_BROWSE }
                KnowledgeModeButton("检索", mode == KNOWLEDGE_MODE_SEARCH, Modifier.weight(1f)) { mode = KNOWLEDGE_MODE_SEARCH }
                KnowledgeModeButton("写入", mode == KNOWLEDGE_MODE_WRITE, Modifier.weight(1f)) { mode = KNOWLEDGE_MODE_WRITE }
            }
        }

        when (mode) {
            KNOWLEDGE_MODE_SEARCH -> {
                item {
                    OutlinedTextField(
                        value = semanticQuery,
                        onValueChange = { semanticQuery = it },
                        label = { Text("自然语言检索") },
                        placeholder = { Text("例如：之前关于 Android 图片识别卡住最后是怎么修的？") },
                        minLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Text(
                        "检索会综合主题、摘要、关键词和受控原文片段进行本地相关度排序。",
                        color = Muted,
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 5.dp),
                    )
                }
                val hits = KnowledgeEngine.search(entries, semanticQuery, limit = 40)
                if (semanticQuery.isNotBlank() && hits.isEmpty()) item { KnowledgeEmpty("没有找到相关知识") }
                items(hits, key = { "search-${it.entry.id}" }) { hit ->
                    KnowledgeCard(hit.entry) {
                        store.archive(hit.entry.id)
                        onChanged()
                    }
                }
            }

            KNOWLEDGE_MODE_WRITE -> {
                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Text("手动写入长期知识", fontWeight = FontWeight.Bold)
                            Text("手动知识默认可被拥有“读取知识库”权限的员工召回。", color = Muted)
                            OutlinedTextField(
                                manualTitle,
                                { manualTitle = it },
                                label = { Text("标题（可选）") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                manualContent,
                                { manualContent = it },
                                label = { Text("知识内容") },
                                minLines = 6,
                                modifier = Modifier.fillMaxWidth(),
                            )
                            OutlinedTextField(
                                manualKeywords,
                                { manualKeywords = it },
                                label = { Text("关键词（可选，用逗号分隔）") },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Text("分类", fontWeight = FontWeight.SemiBold)
                            KnowledgeRoomPicker(manualRoom) { manualRoom = it }
                            Button(
                                enabled = manualContent.isNotBlank(),
                                onClick = {
                                    val entry = store.addManual(
                                        repo = repo,
                                        title = manualTitle,
                                        content = manualContent,
                                        room = manualRoom,
                                        keywords = manualKeywords.split(',', '，').map(String::trim).filter(String::isNotBlank),
                                    )
                                    manualTitle = ""
                                    manualContent = ""
                                    manualKeywords = ""
                                    manualRoom = KnowledgeRooms.GENERAL
                                    onChanged()
                                    scope.launch {
                                        KnowledgeOrganizer.enrich(store, entry.id)
                                        onChanged()
                                    }
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) { Text("保存到知识库") }
                        }
                    }
                }
            }

            else -> {
                item {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        label = { Text("搜索主题、摘要、关键词或来源") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                }
                item { KnowledgeFilterRow(roomFilter) { roomFilter = it } }
                val filteredBase = if (roomFilter == "all") entries else entries.filter { it.room == roomFilter }
                val browseEntries = KnowledgeEngine.search(filteredBase, query, limit = 80).map { it.entry }
                if (browseEntries.isEmpty()) item { KnowledgeEmpty("知识库还是空的，聊天后会自动沉淀到这里") }
                items(browseEntries, key = { "browse-${it.id}" }) { entry ->
                    KnowledgeCard(entry) {
                        store.archive(entry.id)
                        onChanged()
                    }
                }

                item {
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp)) {
                            Text("工作项目", fontWeight = FontWeight.Bold)
                            Text("原来的“工作”能力保留在知识库中，项目资料、附件分析和方案文档不会丢失。", color = Muted)
                            Button(onClick = onNewProject, modifier = Modifier.padding(top = 8.dp)) { Text("＋ 新建工作") }
                        }
                    }
                }
                items(repo.projects(), key = { "project-${it.id}" }) { project ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp)) {
                            Text(project.title, fontWeight = FontWeight.Bold)
                            Text(
                                project.description.ifBlank { "暂无工作描述" },
                                color = Muted,
                                maxLines = 3,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.padding(top = 5.dp),
                            )
                            Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text("资料 ${repo.assets(project.id).size} 份", color = Muted)
                                TextButton(onClick = { onOpenProject(project.id) }) { Text("打开工作") }
                            }
                        }
                    }
                }
            }
        }

        item { Spacer(Modifier.width(1.dp)) }
    }
}

@Composable
private fun KnowledgeModeButton(label: String, selected: Boolean, modifier: Modifier, onClick: () -> Unit) {
    if (selected) {
        Button(onClick = onClick, modifier = modifier) { Text(label) }
    } else {
        OutlinedButton(onClick = onClick, modifier = modifier) { Text(label) }
    }
}

@Composable
private fun KnowledgeMetric(label: String, value: String, modifier: Modifier = Modifier) {
    Card(modifier) {
        Column(Modifier.padding(11.dp)) {
            Text(label, color = Muted, style = MaterialTheme.typography.bodySmall)
            Text(value, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 2.dp))
        }
    }
}

@Composable
private fun KnowledgeFilterRow(selected: String, onSelected: (String) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        val rooms = listOf("all") + KnowledgeRooms.ordered
        rooms.forEach { room ->
            val label = if (room == "all") "全部" else KnowledgeRooms.label(room)
            TextButton(onClick = { onSelected(room) }) {
                Text(if (selected == room) "✓ $label" else label)
            }
        }
    }
}

@Composable
private fun KnowledgeRoomPicker(selected: String, onSelected: (String) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        KnowledgeRooms.ordered.forEach { room ->
            TextButton(onClick = { onSelected(room) }) {
                val label = KnowledgeRooms.label(room)
                Text(if (selected == room) "✓ $label" else label)
            }
        }
    }
}

@Composable
private fun KnowledgeCard(entry: KnowledgeEntry, onArchive: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(KnowledgeRooms.label(entry.room), color = Blue, style = MaterialTheme.typography.labelMedium)
                Text(if (entry.sharedForAgents) "员工可召回" else "仅归档", color = if (entry.sharedForAgents) Emerald else Muted)
            }
            Text(entry.title.ifBlank { "未命名知识" }, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 5.dp))
            Text(entry.summary, color = Muted, modifier = Modifier.padding(top = 5.dp))
            if (entry.keywords.isNotEmpty()) {
                Text(
                    entry.keywords.take(10).joinToString(prefix = "关键词：", separator = " · "),
                    color = Muted,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
            Row(Modifier.fillMaxWidth().padding(top = 6.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                val source = entry.sourceEmployeeName.ifBlank { if (entry.source == "manual") "手动写入" else entry.source }
                Text(source, color = Muted, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = onArchive) { Text("归档") }
            }
        }
    }
}

@Composable
private fun KnowledgeEmpty(text: String) {
    Card(Modifier.fillMaxWidth()) {
        Text(text, color = Muted, modifier = Modifier.padding(18.dp))
    }
}
