package com.b8vipvip.fdex.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.b8vipvip.fdex.network.RealtimeVoiceEvent
import com.b8vipvip.fdex.network.RealtimeVoiceSession

@Composable
internal fun RealtimeVoiceDialog(
    employeeName: String,
    system: String?,
    onDismiss: () -> Unit,
    onAssistantReply: (String) -> Unit,
) {
    val context = LocalContext.current
    var permissionGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        )
    }
    var status by remember { mutableStateOf("等待麦克风权限") }
    var providerInfo by remember { mutableStateOf("") }
    var userTranscript by remember { mutableStateOf("") }
    var assistantTranscript by remember { mutableStateOf("") }
    var currentReply by remember { mutableStateOf("") }
    var session by remember { mutableStateOf<RealtimeVoiceSession?>(null) }

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionGranted = granted
        if (!granted) status = "需要麦克风权限才能实时语音对话"
    }

    LaunchedEffect(Unit) {
        if (!permissionGranted) permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    DisposableEffect(permissionGranted, system) {
        if (permissionGranted) {
            val created = RealtimeVoiceSession(context, system) { event ->
                when (event) {
                    is RealtimeVoiceEvent.Status -> status = event.value
                    is RealtimeVoiceEvent.Ready -> {
                        providerInfo = listOf(event.provider, event.model).filter { it.isNotBlank() }.joinToString(" · ")
                        status = "正在听…"
                    }
                    is RealtimeVoiceEvent.UserTranscript -> userTranscript = event.text
                    is RealtimeVoiceEvent.AssistantTranscript -> {
                        currentReply += event.delta
                        assistantTranscript = currentReply
                    }
                    RealtimeVoiceEvent.Done -> {
                        val reply = currentReply.trim()
                        if (reply.isNotBlank()) onAssistantReply(reply)
                        currentReply = ""
                        status = "正在听…"
                    }
                    is RealtimeVoiceEvent.Error -> status = event.message
                }
            }
            session = created
            created.start()
        }
        onDispose {
            session?.stop()
            session = null
        }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("与 $employeeName 实时语音") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(
                        onClick = {},
                        enabled = false,
                        modifier = Modifier.padding(8.dp),
                    ) {
                        Icon(Icons.Default.Mic, contentDescription = null)
                    }
                }
                Text(status, style = MaterialTheme.typography.titleSmall)
                if (providerInfo.isNotBlank()) {
                    Text(providerInfo, color = Muted, style = MaterialTheme.typography.labelMedium)
                }
                if (userTranscript.isNotBlank()) {
                    Column {
                        Text("我", color = Muted, style = MaterialTheme.typography.labelSmall)
                        Text(userTranscript)
                    }
                }
                if (assistantTranscript.isNotBlank()) {
                    Column {
                        Text(employeeName, color = Muted, style = MaterialTheme.typography.labelSmall)
                        Text(assistantTranscript)
                    }
                }
                if (!permissionGranted) {
                    Button(onClick = { permissionLauncher.launch(Manifest.permission.RECORD_AUDIO) }) {
                        Text("授予麦克风权限")
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    session?.stop()
                    onDismiss()
                },
                shape = CircleShape,
            ) {
                Icon(Icons.Default.Stop, contentDescription = null)
                Text("结束", modifier = Modifier.padding(start = 6.dp))
            }
        },
    )
}
