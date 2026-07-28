package com.b8vipvip.fdex.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Divider
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.b8vipvip.fdex.data.Employee
import com.b8vipvip.fdex.data.GroupMessage
import com.b8vipvip.fdex.data.Project
import com.b8vipvip.fdex.data.ProjectAsset
import com.b8vipvip.fdex.data.Report

internal val PageBg = Color(0xFFF1F5F9)
internal val Emerald = Color(0xFF059669)
internal val Blue = Color(0xFF2563EB)
internal val Muted = Color(0xFF64748B)

@Composable
internal fun Avatar(icon: String, size: Int = 48) {
    Box(
        Modifier.size(size.dp).background(Color(0xFFD1FAE5), RoundedCornerShape(14.dp)),
        contentAlignment = Alignment.Center,
    ) { Text(icon, fontSize = (size / 2).sp) }
}

internal fun employeeEmoji(employee: Employee): String = when {
    employee.materialManager -> "📚"
    employee.position.contains("研究") -> "🔎"
    employee.position.contains("执行") || employee.position.contains("运营") -> "📋"
    employee.position.contains("销售") -> "🤝"
    else -> "🤖"
}

@Composable
internal fun SectionTitle(text: String) {
    Text(text, fontWeight = FontWeight.SemiBold, color = Muted, modifier = Modifier.padding(vertical = 4.dp))
}

@Composable
internal fun ConversationRow(icon: String, title: String, sub: String, preview: String, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(icon)
            Column(Modifier.weight(1f).padding(start = 12.dp)) {
                Text(title, fontWeight = FontWeight.SemiBold)
                Text(sub, color = Emerald, fontSize = 12.sp)
                Text(preview, color = Muted, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 13.sp)
            }
            Text("›", color = Muted, fontSize = 24.sp)
        }
    }
}

@Composable
internal fun StatusPill(status: String) {
    val label = when (status) {
        "generated" -> "已生成方案"
        "analyzed" -> "已分析"
        else -> "进行中"
    }
    Text(
        label,
        color = Emerald,
        fontSize = 11.sp,
        modifier = Modifier.background(Color(0xFFD1FAE5), CircleShape).padding(horizontal = 9.dp, vertical = 4.dp),
    )
}

internal fun storageLabel(value: String): String = when (value) {
    "local_only" -> "本地模式"
    "cloud" -> "云端模式"
    "temporary" -> "临时分析"
    else -> "混合模式"
}

@Composable
internal fun Metric(label: String, value: String) {
    Column {
        Text(label, fontSize = 11.sp, color = Muted)
        Text(value, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
    }
}

@Composable
internal fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = Muted)
        Text(
            value,
            fontWeight = FontWeight.Medium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(.62f),
        )
    }
}

@Composable
internal fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
internal fun SelectorCard(
    title: String,
    options: List<Pair<String, String>>,
    selected: String,
    onSelect: (String) -> Unit,
) {
    Card {
        Column(Modifier.padding(14.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            options.forEach { (value, label) ->
                Row(
                    Modifier.fillMaxWidth().clickable { onSelect(value) }.padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = selected == value, onCheckedChange = { if (it) onSelect(value) })
                    Text(label)
                }
            }
        }
    }
}

@Composable
internal fun EmptyCard(icon: String, title: String, desc: String, action: String? = null, onClick: (() -> Unit)? = null) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.fillMaxWidth().padding(28.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(icon, fontSize = 36.sp)
            Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 8.dp))
            Text(desc, color = Muted, modifier = Modifier.padding(top = 6.dp))
            if (action != null && onClick != null) {
                Button(onClick = onClick, modifier = Modifier.padding(top = 12.dp)) { Text(action) }
            }
        }
    }
}

@Composable
internal fun MenuCard(items: List<Pair<String, String>>, onClick: (String) -> Unit) {
    Card {
        Column {
            items.forEachIndexed { index, (icon, label) ->
                Row(
                    Modifier.fillMaxWidth().clickable { onClick(label) }.padding(15.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(icon)
                    Text(label, modifier = Modifier.weight(1f).padding(start = 10.dp))
                    Text("›", color = Muted, fontSize = 22.sp)
                }
                if (index != items.lastIndex) Divider()
            }
        }
    }
}

@Composable
internal fun StepCard(step: Int, title: String, desc: String, content: @Composable ColumnScope.() -> Unit) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Box(Modifier.size(28.dp).background(Blue, CircleShape), contentAlignment = Alignment.Center) {
                    Text(step.toString(), color = Color.White, fontWeight = FontWeight.Bold)
                }
                Column(Modifier.padding(start = 10.dp)) {
                    Text(title, fontWeight = FontWeight.Bold)
                    Text(desc, color = Muted, fontSize = 13.sp)
                }
            }
            Column(Modifier.padding(top = 12.dp), content = content)
        }
    }
}

@Composable
internal fun NextActionCard(project: Project, hasReport: Boolean, busy: Boolean, onGenerate: () -> Unit) {
    val analyzed = project.status == "analyzed"
    Card(colors = CardDefaults.cardColors(containerColor = Blue)) {
        Column(Modifier.padding(18.dp)) {
            Text("建议下一步", color = Color.White.copy(alpha = .75f), fontSize = 11.sp)
            Text(
                if (hasReport) "方案已经准备好" else if (analyzed) "资料分析完成，生成完整方案吧" else "先补充需求或上传资料",
                color = Color.White,
                fontWeight = FontWeight.Bold,
                fontSize = 20.sp,
                modifier = Modifier.padding(top = 6.dp),
            )
            Text(
                if (hasReport) "你可以查看方案文档，也可以继续补充信息让 AI 优化。" else if (analyzed) "AI 已提取资料要点，下一步生成可执行方案。" else "信息越完整，生成的方案越准确。",
                color = Color.White.copy(alpha = .9f),
                modifier = Modifier.padding(top = 6.dp),
            )
            if (analyzed && !hasReport) {
                androidx.compose.material3.FilledTonalButton(
                    onClick = onGenerate,
                    enabled = !busy,
                    modifier = Modifier.padding(top = 12.dp),
                ) { Text(if (busy) "生成中…" else "生成方案文档") }
            }
        }
    }
}

@Composable
internal fun AssetRow(asset: ProjectAsset, busy: Boolean, onPrivacy: (String) -> Unit, onAnalyze: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC)),
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
    ) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("📎", fontSize = 22.sp)
                Column(Modifier.weight(1f).padding(start = 8.dp)) {
                    Text(asset.name, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${asset.size / 1024} KB · ${asset.status}", color = Muted, fontSize = 11.sp)
                }
                Box {
                    TextButton(onClick = { menu = true }) {
                        Text(if (asset.privacyDecision.isBlank()) "隐私处理" else "已${privacyLabel(asset.privacyDecision)}")
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        listOf(
                            "desensitize" to "自动脱敏",
                            "temporary" to "临时分析",
                            "local_only" to "仅本地保存",
                            "confirm_upload" to "确认使用原文件",
                        ).forEach { (value, label) ->
                            DropdownMenuItem(
                                text = { Text(label) },
                                onClick = { onPrivacy(value); menu = false },
                            )
                        }
                    }
                }
            }
            Button(
                onClick = onAnalyze,
                enabled = !busy && asset.privacyDecision != "local_only",
                modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            ) { Text(if (asset.analysis.isBlank()) "让 AI 分析" else "重新分析") }
            if (asset.analysis.isNotBlank()) {
                Text(asset.analysis, color = Muted, fontSize = 13.sp, modifier = Modifier.padding(top = 8.dp))
            }
        }
    }
}

private fun privacyLabel(value: String): String = when (value) {
    "desensitize" -> "脱敏"
    "temporary" -> "临时分析"
    "local_only" -> "本地保存"
    else -> "确认使用"
}

@Composable
internal fun ReportCard(report: Report) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Color(0xFFF8FAFC)),
        modifier = Modifier.fillMaxWidth().padding(top = 10.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(report.title, fontWeight = FontWeight.Bold)
            Text(report.content, color = Muted, modifier = Modifier.padding(top = 8.dp))
        }
    }
}

@Composable
internal fun GroupBubble(message: GroupMessage) {
    val user = message.role == "user"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) {
        Card(
            colors = CardDefaults.cardColors(
                containerColor = when {
                    user -> Color(0xFF10B981)
                    message.role == "system" -> Color(0xFFE2E8F0)
                    else -> Color.White
                },
            ),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(.82f),
        ) {
            Column(Modifier.padding(12.dp)) {
                if (!user && message.employeeName.isNotBlank()) Text(message.employeeName, color = Emerald, fontSize = 11.sp)
                Text(message.content, color = if (user) Color.White else MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}
