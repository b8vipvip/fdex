package com.b8vipvip.fdex.ui

import android.Manifest
import android.content.pm.PackageManager
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.VolumeOff
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.b8vipvip.fdex.data.CentralSessionStore
import com.b8vipvip.fdex.network.RealtimeVoiceEvent
import com.b8vipvip.fdex.network.RealtimeVoiceSession

@Composable
internal fun RealtimeVoiceBar(
    employeeName: String,
    system: String?,
    memoryControl: String?,
    modifier: Modifier = Modifier,
    onEnd: () -> Unit,
    onSessionChanged: (RealtimeVoiceSession?) -> Unit = {},
    onUserTranscript: (String) -> Unit,
    onAssistantReply: (String) -> Unit,
) {
    val context = LocalContext.current
    val sessions = remember { CentralSessionStore(context) }
    val accessToken = sessions.accessToken()
    val authenticatedMemoryControl = remember(accessToken, memoryControl) {
        buildRealtimeSessionControl(accessToken, memoryControl)
    }
    var permissionGranted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        )
    }
    var status by remember { mutableStateOf("正在准备实时语音…") }
    var providerInfo by remember { mutableStateOf("") }
    var currentReply by remember { mutableStateOf("") }
    var session by remember { mutableStateOf<RealtimeVoiceSession?>(null) }
    var microphoneEnabled by remember { mutableStateOf(true) }
    var speakerEnabled by remember { mutableStateOf(true) }

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        permissionGranted = granted
        status = if (granted) "正在连接实时语音…" else "需要麦克风权限才能实时语音"
    }

    LaunchedEffect(Unit) {
        if (!permissionGranted) permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    DisposableEffect(permissionGranted, system, authenticatedMemoryControl, accessToken) {
        var created: RealtimeVoiceSession? = null
        if (permissionGranted && accessToken.isBlank()) {
            status = "FDEX 登录状态已失效，请重新登录"
        } else if (permissionGranted) {
            created = RealtimeVoiceSession(context, system, authenticatedMemoryControl) { event ->
                when (event) {
                    is RealtimeVoiceEvent.Status -> status = event.value
                    is RealtimeVoiceEvent.Ready -> {
                        providerInfo = listOf(event.provider, event.model).filter { it.isNotBlank() }.joinToString(" · ")
                        status = "正在听…"
                    }
                    is RealtimeVoiceEvent.UserTranscript -> {
                        val transcript = event.text.trim()
                        if (transcript.isNotBlank()) onUserTranscript(transcript)
                    }
                    is RealtimeVoiceEvent.AssistantTranscript -> currentReply += event.delta
                    is RealtimeVoiceEvent.Interrupted -> {
                        val partial = currentReply.trim()
                        if (partial.isNotBlank()) onAssistantReply(partial)
                        currentReply = ""
                        status = event.status
                    }
                    RealtimeVoiceEvent.Done -> {
                        val reply = currentReply.trim()
                        if (reply.isNotBlank()) onAssistantReply(reply)
                        currentReply = ""
                        status = if (microphoneEnabled) "正在听…" else "麦克风已关闭"
                    }
                    is RealtimeVoiceEvent.Error -> status = event.message
                }
            }
            session = created
            onSessionChanged(created)
            created.start()
        }
        onDispose {
            created?.stop()
            if (session === created) session = null
            onSessionChanged(null)
        }
    }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        shadowElevation = 8.dp,
        tonalElevation = 2.dp,
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            VoiceWaveform(
                active = permissionGranted && microphoneEnabled,
                modifier = Modifier.width(60.dp).height(32.dp),
            )
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    "$employeeName · ${if (microphoneEnabled) status else "麦克风已关闭"}",
                    style = MaterialTheme.typography.labelLarge,
                    maxLines = 1,
                )
                Text(
                    when {
                        !permissionGranted -> "点击麦克风授权后开始"
                        providerInfo.isNotBlank() -> providerInfo
                        else -> "实时语音连接中"
                    },
                    color = Muted,
                    style = MaterialTheme.typography.labelSmall,
                    maxLines = 1,
                )
            }

            IconButton(
                onClick = {
                    if (!permissionGranted) {
                        permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                    } else {
                        microphoneEnabled = !microphoneEnabled
                        session?.setMicrophoneEnabled(microphoneEnabled)
                        status = if (microphoneEnabled) "正在听…" else "麦克风已关闭"
                    }
                },
            ) {
                Icon(
                    if (microphoneEnabled) Icons.Default.Mic else Icons.Default.MicOff,
                    contentDescription = if (microphoneEnabled) "关闭麦克风" else "开启麦克风",
                )
            }

            IconButton(
                onClick = {
                    speakerEnabled = !speakerEnabled
                    session?.setSpeakerEnabled(speakerEnabled)
                },
            ) {
                Icon(
                    if (speakerEnabled) Icons.Default.VolumeUp else Icons.Default.VolumeOff,
                    contentDescription = if (speakerEnabled) "关闭扬声器" else "开启扬声器",
                )
            }

            IconButton(
                onClick = {
                    session?.stop()
                    onEnd()
                },
            ) {
                Icon(Icons.Default.CallEnd, contentDescription = "结束实时语音")
            }
        }
    }
}

internal fun buildRealtimeSessionControl(accessToken: String, memoryControl: String?): String? {
    val token = accessToken.trim()
    if (token.isBlank()) return memoryControl
    val encoded = Base64.encodeToString(
        token.toByteArray(Charsets.UTF_8),
        Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
    )
    val authMarker = "[[FDEX_AUTH_V1:$encoded]]"
    val memory = memoryControl.orEmpty().trim()
    return if (memory.isBlank()) authMarker else "$authMarker\n$memory"
}

@Composable
private fun VoiceWaveform(active: Boolean, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "voice-wave")
    Row(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(3.dp, Alignment.CenterHorizontally),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        repeat(9) { index ->
            val animatedHeight by transition.animateFloat(
                initialValue = 5f + (index % 3) * 2f,
                targetValue = 14f + ((index * 5) % 12),
                animationSpec = infiniteRepeatable(
                    animation = tween(durationMillis = 340 + index * 32),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "voice-wave-$index",
            )
            Box(
                Modifier
                    .width(3.dp)
                    .height((if (active) animatedHeight else 5f).dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(MaterialTheme.colorScheme.primary),
            )
        }
    }
}
