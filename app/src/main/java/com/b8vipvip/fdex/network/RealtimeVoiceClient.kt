package com.b8vipvip.fdex.network

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Base64
import com.b8vipvip.fdex.BuildConfig
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

sealed interface RealtimeVoiceEvent {
    data class Status(val value: String) : RealtimeVoiceEvent
    data class Ready(
        val provider: String,
        val model: String,
        val inputSampleRate: Int,
        val outputSampleRate: Int,
    ) : RealtimeVoiceEvent
    data class UserTranscript(val text: String) : RealtimeVoiceEvent
    data class AssistantTranscript(val delta: String) : RealtimeVoiceEvent
    data class Interrupted(val status: String) : RealtimeVoiceEvent
    data object Done : RealtimeVoiceEvent
    data class Error(val message: String) : RealtimeVoiceEvent
}

class RealtimeVoiceSession(
    context: Context,
    private val system: String?,
    private val memoryControl: String?,
    private val onEvent: (RealtimeVoiceEvent) -> Unit,
) {
    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val running = AtomicBoolean(false)
    private val ready = AtomicBoolean(false)
    private val microphoneEnabled = AtomicBoolean(true)
    private val speakerEnabled = AtomicBoolean(true)
    private val playbackLock = Any()
    private val receivedAudioFrames = AtomicLong(0)
    private val receivedAudioBytes = AtomicLong(0)
    private val playedAudioFrames = AtomicLong(0)
    private val playedAudioBytes = AtomicLong(0)
    private val sentMicFrames = AtomicLong(0)
    private val sentMicBytes = AtomicLong(0)
    private val httpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var socket: WebSocket? = null
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private var recordThread: Thread? = null
    private var previousAudioMode: Int? = null
    private var previousSpeakerphoneOn: Boolean? = null
    private var previousCommunicationDeviceId: Int? = null
    private var audioFocusRequest: AudioFocusRequest? = null
    private var currentOutputSampleRate: Int = DEFAULT_OUTPUT_SAMPLE_RATE

    private val audioFocusListener = AudioManager.OnAudioFocusChangeListener { change ->
        sendDiagnostic("audio_focus_change", JSONObject().put("change", change))
    }

    fun start() {
        if (!running.compareAndSet(false, true)) return
        ready.set(false)
        microphoneEnabled.set(true)
        speakerEnabled.set(true)
        receivedAudioFrames.set(0)
        receivedAudioBytes.set(0)
        playedAudioFrames.set(0)
        playedAudioBytes.set(0)
        sentMicFrames.set(0)
        sentMicBytes.set(0)
        emit(RealtimeVoiceEvent.Status("正在连接实时语音…"))
        val request = Request.Builder().url(realtimeUrl()).build()
        socket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val payload = JSONObject()
                    .put("type", "start")
                    .put("sample_rate", DEFAULT_OUTPUT_SAMPLE_RATE)
                if (!system.isNullOrBlank()) payload.put("system", system)
                // Opaque FDEX-only ACL/scope metadata. The FDEX server consumes this field
                // before opening the upstream realtime provider; it is never forwarded.
                if (!memoryControl.isNullOrBlank()) payload.put("memory_control", memoryControl)
                webSocket.send(payload.toString())
                sendDiagnostic("client_websocket_open")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleServerEvent(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ready.set(false)
                sendDiagnostic(
                    "client_websocket_failure",
                    JSONObject()
                        .put("message", (t.message ?: t.javaClass.simpleName).take(160))
                        .put("http_code", response?.code ?: 0),
                )
                emit(RealtimeVoiceEvent.Error(t.message ?: "实时语音连接失败"))
                stopInternal(closeSocket = false)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                ready.set(false)
                sendDiagnostic(
                    "client_websocket_closed",
                    JSONObject().put("code", code).put("reason", reason.take(120)),
                )
                if (running.get()) emit(RealtimeVoiceEvent.Status("实时语音已结束"))
                stopInternal(closeSocket = false)
            }
        })
    }

    fun stop() {
        sendDiagnostic("client_stop_requested")
        socket?.send("{\"type\":\"stop\"}")
        stopInternal(closeSocket = true)
    }

    fun sendText(text: String): Boolean {
        val value = text.trim()
        if (value.isBlank() || !running.get() || !ready.get()) return false
        return socket?.send(JSONObject().put("type", "text").put("text", value).toString()) ?: false
    }

    fun setMicrophoneEnabled(enabled: Boolean) {
        microphoneEnabled.set(enabled)
        sendDiagnostic("microphone_toggle", JSONObject().put("enabled", enabled))
    }

    fun setSpeakerEnabled(enabled: Boolean) {
        speakerEnabled.set(enabled)
        val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (audioRecord != null || audioTrack != null) applyCommunicationRoute(audioManager, enabled)
    }

    private fun handleServerEvent(raw: String) {
        val json = runCatching { JSONObject(raw) }.getOrNull() ?: return
        when (json.optString("type")) {
            "ready" -> {
                val inputRate = sanitizeSampleRate(
                    json.optInt("input_sample_rate", DEFAULT_INPUT_SAMPLE_RATE),
                    DEFAULT_INPUT_SAMPLE_RATE,
                )
                val outputRate = sanitizeSampleRate(
                    json.optInt("output_sample_rate", json.optInt("sample_rate", DEFAULT_OUTPUT_SAMPLE_RATE)),
                    DEFAULT_OUTPUT_SAMPLE_RATE,
                )
                currentOutputSampleRate = outputRate
                ready.set(true)
                emit(
                    RealtimeVoiceEvent.Ready(
                        json.optString("provider"),
                        json.optString("model"),
                        inputRate,
                        outputRate,
                    )
                )
                startAudio(inputRate, outputRate)
            }
            "status" -> emit(RealtimeVoiceEvent.Status(json.optString("status")))
            "interrupt" -> {
                clearPlayback()
                emit(
                    RealtimeVoiceEvent.Interrupted(
                        json.optString("status").ifBlank { "回答已打断，正在听…" }
                    )
                )
            }
            "audio" -> {
                val encoded = json.optString("delta")
                if (encoded.isNotBlank()) {
                    val decoded = runCatching { Base64.decode(encoded, Base64.DEFAULT) }.getOrNull()
                    if (decoded == null) {
                        sendDiagnostic("audio_base64_decode_failed", JSONObject().put("encoded_chars", encoded.length))
                    } else if (decoded.isNotEmpty()) {
                        val frames = receivedAudioFrames.incrementAndGet()
                        val bytes = receivedAudioBytes.addAndGet(decoded.size.toLong())
                        if (frames == 1L || frames % 50L == 0L) {
                            sendDiagnostic(
                                "downlink_audio_received",
                                JSONObject()
                                    .put("frames", frames)
                                    .put("bytes", bytes)
                                    .put("chunk_bytes", decoded.size)
                                    .put("sample_rate", currentOutputSampleRate),
                            )
                        }
                        playAudio(decoded)
                    }
                }
            }
            "user_transcript" -> emit(RealtimeVoiceEvent.UserTranscript(json.optString("text")))
            "assistant_transcript" -> emit(RealtimeVoiceEvent.AssistantTranscript(json.optString("delta")))
            "done" -> emit(RealtimeVoiceEvent.Done)
            "error" -> emit(RealtimeVoiceEvent.Error(json.optString("message").ifBlank { "实时语音发生错误" }))
        }
    }

    @Suppress("MissingPermission", "DEPRECATION")
    private fun startAudio(inputSampleRate: Int, outputSampleRate: Int) {
        if (!running.get() || audioRecord != null) return
        val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        previousAudioMode = audioManager.mode
        previousSpeakerphoneOn = audioManager.isSpeakerphoneOn
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) previousCommunicationDeviceId = audioManager.communicationDevice?.id
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION

        val communicationAttributes = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val focusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(communicationAttributes)
                .setOnAudioFocusChangeListener(audioFocusListener)
                .build()
            audioFocusRequest = focusRequest
            val focusResult = runCatching { audioManager.requestAudioFocus(focusRequest) }
                .getOrDefault(AudioManager.AUDIOFOCUS_REQUEST_FAILED)
            sendDiagnostic("audio_focus_request", JSONObject().put("result", focusResult))
        }
        applyCommunicationRoute(audioManager, speakerEnabled.get())

        val minRecord = AudioRecord.getMinBufferSize(
            inputSampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(4096)
        val recorder = AudioRecord(
            MediaRecorder.AudioSource.VOICE_COMMUNICATION,
            inputSampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            minRecord * 2,
        )
        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            sendDiagnostic("audio_record_init_failed", JSONObject().put("sample_rate", inputSampleRate))
            emit(RealtimeVoiceEvent.Error("麦克风初始化失败（${inputSampleRate}Hz）"))
            return
        }
        audioRecord = recorder
        if (AcousticEchoCanceler.isAvailable()) {
            echoCanceler = runCatching { AcousticEchoCanceler.create(recorder.audioSessionId) }
                .getOrNull()
                ?.apply { enabled = true }
        }

        val minPlay = AudioTrack.getMinBufferSize(
            outputSampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(4096)
        val track = runCatching {
            AudioTrack.Builder()
                .setAudioAttributes(communicationAttributes)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(outputSampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build()
                )
                .setBufferSizeInBytes(minPlay * 4)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
        }.getOrElse { error ->
            sendDiagnostic(
                "audio_track_build_failed",
                JSONObject().put("sample_rate", outputSampleRate).put("message", (error.message ?: "build failed").take(120)),
            )
            emit(RealtimeVoiceEvent.Error("语音播放初始化失败（${outputSampleRate}Hz）"))
            return
        }
        if (track.state != AudioTrack.STATE_INITIALIZED) {
            val badState = track.state
            track.release()
            sendDiagnostic("audio_track_init_failed", JSONObject().put("sample_rate", outputSampleRate).put("state", badState))
            emit(RealtimeVoiceEvent.Error("语音播放设备初始化失败（${outputSampleRate}Hz）"))
            return
        }
        audioTrack = track
        runCatching { track.setVolume(1.0f) }
        val playOk = runCatching { track.play(); true }.getOrDefault(false)
        sendDiagnostic(
            "audio_track_ready",
            JSONObject()
                .put("sample_rate", outputSampleRate)
                .put("buffer_bytes", minPlay * 4)
                .put("state", track.state)
                .put("play_state", track.playState)
                .put("play_ok", playOk)
                .put("speaker_enabled", speakerEnabled.get())
                .put("route", currentCommunicationRoute(audioManager)),
        )
        if (!playOk) {
            emit(RealtimeVoiceEvent.Error("语音播放设备启动失败"))
            return
        }

        recorder.startRecording()
        sendDiagnostic(
            "audio_record_ready",
            JSONObject()
                .put("sample_rate", inputSampleRate)
                .put("buffer_bytes", minRecord * 2)
                .put("aec_available", AcousticEchoCanceler.isAvailable())
                .put("aec_enabled", echoCanceler?.enabled == true),
        )
        recordThread = Thread({ recordLoop(recorder, minRecord) }, "fdex-realtime-mic").apply { start() }
        emit(RealtimeVoiceEvent.Status("正在听…"))
    }

    private fun recordLoop(recorder: AudioRecord, bufferSize: Int) {
        val buffer = ByteArray(bufferSize.coerceAtMost(8192))
        while (running.get()) {
            val count = recorder.read(buffer, 0, buffer.size)
            if (count <= 0) continue
            if (!microphoneEnabled.get()) continue
            val encoded = Base64.encodeToString(buffer.copyOf(count), Base64.NO_WRAP)
            val ok = socket?.send(JSONObject().put("type", "audio").put("data", encoded).toString()) ?: false
            if (!ok) break
            val frames = sentMicFrames.incrementAndGet()
            val bytes = sentMicBytes.addAndGet(count.toLong())
            if (frames == 1L || frames % 100L == 0L) {
                sendDiagnostic("uplink_audio_sent", JSONObject().put("frames", frames).put("bytes", bytes).put("chunk_bytes", count))
            }
        }
    }

    private fun playAudio(bytes: ByteArray) {
        synchronized(playbackLock) {
            val track = audioTrack
            if (track == null) {
                sendDiagnostic("playback_no_track", JSONObject().put("chunk_bytes", bytes.size))
                return
            }
            if (track.state != AudioTrack.STATE_INITIALIZED) {
                sendDiagnostic("playback_track_not_initialized", JSONObject().put("state", track.state))
                return
            }
            val written = runCatching { track.write(bytes, 0, bytes.size, AudioTrack.WRITE_BLOCKING) }
                .getOrElse { error ->
                    sendDiagnostic(
                        "playback_write_exception",
                        JSONObject().put("message", (error.message ?: error.javaClass.simpleName).take(120)).put("chunk_bytes", bytes.size),
                    )
                    return
                }
            if (written <= 0) {
                sendDiagnostic("playback_write_failed", JSONObject().put("result", written).put("chunk_bytes", bytes.size))
                return
            }
            val frames = playedAudioFrames.incrementAndGet()
            val total = playedAudioBytes.addAndGet(written.toLong())
            if (frames == 1L || frames % 50L == 0L) {
                sendDiagnostic(
                    "playback_progress",
                    JSONObject()
                        .put("frames", frames)
                        .put("bytes", total)
                        .put("last_written", written)
                        .put("play_state", track.playState)
                        .put("sample_rate", currentOutputSampleRate),
                )
            }
        }
    }

    private fun clearPlayback() {
        synchronized(playbackLock) {
            val track = audioTrack ?: return
            runCatching {
                if (track.playState == AudioTrack.PLAYSTATE_PLAYING) track.pause()
                track.flush()
                track.play()
            }
            sendDiagnostic(
                "playback_flushed_for_interrupt",
                JSONObject()
                    .put("received_frames", receivedAudioFrames.get())
                    .put("received_bytes", receivedAudioBytes.get())
                    .put("played_frames", playedAudioFrames.get())
                    .put("played_bytes", playedAudioBytes.get()),
            )
        }
    }

    @Suppress("DEPRECATION")
    private fun applyCommunicationRoute(audioManager: AudioManager, speaker: Boolean) {
        var routeApplied = false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val preferredType = if (speaker) AudioDeviceInfo.TYPE_BUILTIN_SPEAKER else AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            val target = audioManager.availableCommunicationDevices.firstOrNull { it.type == preferredType }
            routeApplied = if (target != null) runCatching { audioManager.setCommunicationDevice(target) }.getOrDefault(false) else false
            if (!routeApplied && !speaker) runCatching { audioManager.clearCommunicationDevice() }
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || !routeApplied) runCatching { audioManager.isSpeakerphoneOn = speaker }
        sendDiagnostic(
            "audio_route",
            JSONObject()
                .put("speaker_enabled", speaker)
                .put("modern_route_applied", routeApplied)
                .put("route", currentCommunicationRoute(audioManager)),
        )
    }

    @Suppress("DEPRECATION")
    private fun currentCommunicationRoute(audioManager: AudioManager): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val device = audioManager.communicationDevice
            if (device != null) return "${device.type}:${device.productName}"
        }
        return if (audioManager.isSpeakerphoneOn) "legacy:speaker" else "legacy:communication"
    }

    @Suppress("DEPRECATION")
    private fun stopInternal(closeSocket: Boolean) {
        ready.set(false)
        if (!running.getAndSet(false) && audioRecord == null && audioTrack == null) return
        sendDiagnostic(
            "client_audio_summary",
            JSONObject()
                .put("mic_frames", sentMicFrames.get())
                .put("mic_bytes", sentMicBytes.get())
                .put("received_frames", receivedAudioFrames.get())
                .put("received_bytes", receivedAudioBytes.get())
                .put("played_frames", playedAudioFrames.get())
                .put("played_bytes", playedAudioBytes.get()),
        )
        runCatching { audioRecord?.stop() }
        runCatching { recordThread?.join(400) }
        echoCanceler?.release()
        echoCanceler = null
        audioRecord?.release()
        audioRecord = null
        synchronized(playbackLock) {
            runCatching { audioTrack?.stop() }
            audioTrack?.release()
            audioTrack = null
        }
        val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val previousId = previousCommunicationDeviceId
            val previousDevice = previousId?.let { id -> audioManager.availableCommunicationDevices.firstOrNull { it.id == id } }
            if (previousDevice != null) runCatching { audioManager.setCommunicationDevice(previousDevice) }
            else runCatching { audioManager.clearCommunicationDevice() }
        } else {
            previousSpeakerphoneOn?.let { previous -> runCatching { audioManager.isSpeakerphoneOn = previous } }
        }
        previousCommunicationDeviceId = null
        previousSpeakerphoneOn = null
        audioFocusRequest?.let { request -> runCatching { audioManager.abandonAudioFocusRequest(request) } }
        audioFocusRequest = null
        previousAudioMode?.let { audioManager.mode = it }
        previousAudioMode = null
        if (closeSocket) socket?.close(1000, "client stop")
        socket = null
    }

    private fun sendDiagnostic(event: String, details: JSONObject = JSONObject()) {
        socket?.send(JSONObject().put("type", "diagnostic").put("event", event).put("details", details).toString())
    }

    private fun emit(event: RealtimeVoiceEvent) {
        mainHandler.post { onEvent(event) }
    }

    private fun realtimeUrl(): String {
        val base = BuildConfig.SERVER_BASE_URL.trimEnd('/')
        val websocketBase = when {
            base.startsWith("https://") -> "wss://${base.removePrefix("https://")}"
            base.startsWith("http://") -> "ws://${base.removePrefix("http://")}"
            else -> base
        }
        return "$websocketBase/api/client/voice/realtime"
    }

    private fun sanitizeSampleRate(value: Int, fallback: Int): Int = value.takeIf { it in 8_000..48_000 } ?: fallback

    companion object {
        private const val DEFAULT_INPUT_SAMPLE_RATE = 24_000
        private const val DEFAULT_OUTPUT_SAMPLE_RATE = 24_000
    }
}
