package com.b8vipvip.fdex.ui

import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AudioFile
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.VideoFile
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.network.ChatAttachment
import com.b8vipvip.fdex.network.ChatAttachmentKind
import com.b8vipvip.fdex.network.chatAttachmentFromUri
import com.b8vipvip.fdex.network.encodeChatContent
import com.b8vipvip.fdex.network.formatAttachmentSize
import com.b8vipvip.fdex.network.parseChatContent
import com.b8vipvip.fdex.network.persistChatAttachmentPermission

private const val MAX_CHAT_ATTACHMENTS = 6

@Composable
internal fun AttachmentChatComposer(
    value: String,
    placeholder: String,
    busy: Boolean,
    onValueChange: (String) -> Unit,
    onSend: (String) -> Unit,
    onRealtimeVoice: (() -> Unit)? = null,
) {
    val context = LocalContext.current
    var pending by remember { mutableStateOf<List<ChatAttachment>>(emptyList()) }
    var menuOpen by remember { mutableStateOf(false) }
    var pendingKind by remember { mutableStateOf(ChatAttachmentKind.FILE) }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null && pending.size < MAX_CHAT_ATTACHMENTS) {
            persistChatAttachmentPermission(context, uri)
            val item = chatAttachmentFromUri(context, uri, pendingKind)
            if (pending.none { it.uri == item.uri }) pending = pending + item
        }
    }

    Column(Modifier.fillMaxWidth().background(androidx.compose.ui.graphics.Color.White)) {
        if (pending.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(start = 10.dp, end = 10.dp, top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                pending.forEach { attachment ->
                    PendingAttachmentChip(
                        attachment = attachment,
                        onRemove = { pending = pending.filterNot { it.uri == attachment.uri } },
                    )
                }
            }
        }

        Row(
            Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                IconButton(
                    enabled = !busy,
                    onClick = { menuOpen = true },
                ) {
                    Icon(Icons.Default.Add, contentDescription = "添加附件")
                }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    if (onRealtimeVoice != null) {
                        AttachmentMenuItem("实时语音通话", Icons.Default.Mic) {
                            menuOpen = false
                            onRealtimeVoice()
                        }
                    }
                    AttachmentMenuItem("图片", Icons.Default.Image) {
                        pendingKind = ChatAttachmentKind.IMAGE
                        menuOpen = false
                        picker.launch(arrayOf("image/*"))
                    }
                    AttachmentMenuItem("视频", Icons.Default.VideoFile) {
                        pendingKind = ChatAttachmentKind.VIDEO
                        menuOpen = false
                        picker.launch(arrayOf("video/*"))
                    }
                    AttachmentMenuItem("语音", Icons.Default.AudioFile) {
                        pendingKind = ChatAttachmentKind.AUDIO
                        menuOpen = false
                        picker.launch(arrayOf("audio/*"))
                    }
                    AttachmentMenuItem("文件", Icons.Default.Description) {
                        pendingKind = ChatAttachmentKind.FILE
                        menuOpen = false
                        picker.launch(arrayOf("*/*"))
                    }
                }
            }

            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text(placeholder) },
                modifier = Modifier.weight(1f),
                maxLines = 4,
                shape = RoundedCornerShape(22.dp),
            )
            if (onRealtimeVoice != null) {
                IconButton(
                    enabled = !busy,
                    onClick = onRealtimeVoice,
                ) {
                    Icon(Icons.Default.Mic, contentDescription = "实时语音对话")
                }
            }
            Spacer(Modifier.width(4.dp))
            Button(
                enabled = !busy && (value.isNotBlank() || pending.isNotEmpty()),
                onClick = {
                    val encoded = encodeChatContent(value, pending)
                    if (encoded.isNotBlank()) {
                        onSend(encoded)
                        pending = emptyList()
                    }
                },
                shape = RoundedCornerShape(22.dp),
            ) { Text("发送") }
        }
    }
}

@Composable
private fun AttachmentMenuItem(
    label: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit,
) {
    DropdownMenuItem(
        text = { Text(label) },
        leadingIcon = { Icon(icon, contentDescription = null) },
        onClick = onClick,
    )
}

@Composable
private fun PendingAttachmentChip(attachment: ChatAttachment, onRemove: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(12.dp),
        tonalElevation = 1.dp,
    ) {
        Row(
            modifier = Modifier.padding(start = 10.dp, top = 6.dp, bottom = 6.dp, end = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(attachmentIcon(attachment.kind), contentDescription = null)
            Column(Modifier.padding(start = 6.dp)) {
                Text(attachment.name.take(28), style = MaterialTheme.typography.labelMedium, maxLines = 1)
                Text(formatAttachmentSize(attachment.size), color = Muted, style = MaterialTheme.typography.labelSmall)
            }
            IconButton(onClick = onRemove) {
                Icon(Icons.Default.Close, contentDescription = "移除附件")
            }
        }
    }
}

@Composable
internal fun AttachmentUserMessage(content: String) {
    val context = LocalContext.current
    val parsed = remember(content) { parseChatContent(content) }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Surface(
            modifier = Modifier.fillMaxWidth(.88f),
            shape = RoundedCornerShape(18.dp),
            color = androidx.compose.ui.graphics.Color(0xFFE8F2FF),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                if (parsed.text.isNotBlank()) {
                    Text(
                        parsed.text,
                        color = MaterialTheme.colorScheme.onSurface,
                        style = MaterialTheme.typography.bodyLarge,
                    )
                }
                if (parsed.attachments.isNotEmpty()) {
                    if (parsed.text.isNotBlank()) Spacer(Modifier.width(6.dp))
                    parsed.attachments.forEach { attachment ->
                        Surface(
                            onClick = {
                                runCatching {
                                    val intent = Intent(Intent.ACTION_VIEW).apply {
                                        setDataAndType(Uri.parse(attachment.uri), attachment.mimeType)
                                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                    }
                                    context.startActivity(intent)
                                }
                            },
                            modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                            shape = RoundedCornerShape(12.dp),
                            color = androidx.compose.ui.graphics.Color.White.copy(alpha = .72f),
                        ) {
                            Row(
                                modifier = Modifier.padding(10.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Icon(attachmentIcon(attachment.kind), contentDescription = null)
                                Column(Modifier.padding(start = 8.dp).weight(1f)) {
                                    Text(attachment.name, fontWeight = FontWeight.Medium, maxLines = 1)
                                    Text(
                                        "${attachmentLabel(attachment.kind)} · ${formatAttachmentSize(attachment.size)}",
                                        color = Muted,
                                        style = MaterialTheme.typography.labelSmall,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun attachmentIcon(kind: ChatAttachmentKind) = when (kind) {
    ChatAttachmentKind.IMAGE -> Icons.Default.Image
    ChatAttachmentKind.VIDEO -> Icons.Default.VideoFile
    ChatAttachmentKind.AUDIO -> Icons.Default.AudioFile
    ChatAttachmentKind.FILE -> Icons.Default.Description
}

private fun attachmentLabel(kind: ChatAttachmentKind): String = when (kind) {
    ChatAttachmentKind.IMAGE -> "图片"
    ChatAttachmentKind.VIDEO -> "视频"
    ChatAttachmentKind.AUDIO -> "语音"
    ChatAttachmentKind.FILE -> "文件"
}
