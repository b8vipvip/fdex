package com.b8vipvip.fdex.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.FormatQuote
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.b8vipvip.fdex.data.ChatMessage
import com.b8vipvip.fdex.data.GroupMessage

private val CopyActionColor = Color(0xFF64748B)
private val QuoteActionColor = Color(0xFF2563EB)
private val DeleteActionColor = Color(0xFFDC2626)

@Composable
internal fun ActionableEmployeeChatMessage(
    message: ChatMessage,
    employeeName: String,
    onDelete: () -> Unit,
    onQuote: (author: String, content: String) -> Unit,
) {
    val user = message.role == "user"
    Column(Modifier.fillMaxWidth()) {
        SelectableMessageBody {
            if (user) {
                AttachmentUserMessage(message.content)
            } else {
                RichAssistantMessage(message.content, modifier = Modifier.fillMaxWidth())
            }
        }
        MessageActionBar(
            content = message.content,
            alignEnd = user,
            onDelete = onDelete,
            onQuote = {
                onQuote(if (user) "我" else employeeName, message.content)
            },
        )
    }
}

@Composable
internal fun ActionableGroupChatMessage(
    message: GroupMessage,
    onDelete: () -> Unit,
    onQuote: (author: String, content: String) -> Unit,
) {
    val user = message.role == "user"
    Column(Modifier.fillMaxWidth()) {
        when (message.role) {
            "user" -> SelectableMessageBody {
                AttachmentUserMessage(message.content)
            }
            "system" -> SelectableMessageBody {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    Surface(
                        shape = RoundedCornerShape(14.dp),
                        color = Color(0xFFF1F5F9),
                    ) {
                        Text(
                            message.content,
                            color = Muted,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp),
                        )
                    }
                }
            }
            else -> {
                if (message.employeeName.isNotBlank()) {
                    Text(
                        message.employeeName,
                        color = Muted,
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier.padding(bottom = 5.dp),
                    )
                }
                SelectableMessageBody {
                    RichAssistantMessage(message.content, modifier = Modifier.fillMaxWidth())
                }
            }
        }
        MessageActionBar(
            content = message.content,
            alignEnd = user,
            onDelete = onDelete,
            onQuote = {
                val author = when (message.role) {
                    "user" -> "我"
                    "system" -> "群系统"
                    else -> message.employeeName.ifBlank { "智体" }
                }
                onQuote(author, message.content)
            },
        )
    }
}

@Composable
internal fun SelectableMessageBody(content: @Composable () -> Unit) {
    SelectionContainer(content = content)
}

@Composable
private fun MessageActionBar(
    content: String,
    alignEnd: Boolean,
    onDelete: () -> Unit,
    onQuote: () -> Unit,
) {
    val context = LocalContext.current
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 2.dp),
        horizontalArrangement = if (alignEnd) Arrangement.End else Arrangement.Start,
    ) {
        MessageActionIcon(
            imageVector = Icons.Default.ContentCopy,
            contentDescription = "复制消息",
            tint = CopyActionColor,
            onClick = {
                val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                clipboard?.setPrimaryClip(ClipData.newPlainText("FDEX 聊天消息", visibleMessageText(content)))
            },
        )
        MessageActionIcon(
            imageVector = Icons.Default.FormatQuote,
            contentDescription = "引用消息",
            tint = QuoteActionColor,
            onClick = onQuote,
        )
        MessageActionIcon(
            imageVector = Icons.Default.Delete,
            contentDescription = "删除消息",
            tint = DeleteActionColor,
            onClick = onDelete,
        )
    }
}

@Composable
private fun MessageActionIcon(
    imageVector: ImageVector,
    contentDescription: String,
    tint: Color,
    onClick: () -> Unit,
) {
    IconButton(
        onClick = onClick,
        modifier = Modifier.size(34.dp),
        colors = IconButtonDefaults.iconButtonColors(contentColor = tint),
    ) {
        Icon(
            imageVector = imageVector,
            contentDescription = contentDescription,
            modifier = Modifier.size(18.dp),
        )
    }
}
