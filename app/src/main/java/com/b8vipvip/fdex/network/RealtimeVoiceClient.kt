package com.b8vipvip.fdex.network

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
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
    data object Done : RealtimeVoiceEvent
    data class Error(val message: String) : RealtimeVoiceEvent
}

class RealtimeVoiceSession(
    context: Context,
    private val system: String?,
    private val onEvent: (RealtimeVoiceEvent) -> Unit,
) {
    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val running = AtomicBoolean(false)
    private val microphoneEnabled = AtomicBoolean(true)
    private val speakerEnabled = AtomicBoolean(true)
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

    fun start() {
        if (!running.compareAndSet(false, true)) return
        microphoneEnabled.set(true)
        speakerEnabled.set(true)
        emit(RealtimeVoiceEvent.Status("正在连接实时语音…"))
        val request = Request.Builder().url(realtimeUrl()).build()
        socket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val payload = JSONObject()
                    .put("type", "start")
                    .put("sample_rate", DEFAULT_OUTPUT_SAMPLE_RATE)
                if (!system.isNullOrBlank()) payload.put("system", system)
                webSocket.send(payload.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleServerEvent(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                emit(RealtimeVoiceEvent.Error(t.message ?: "实时语音连接失败"))
                stopInternal(closeSocket = false)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (running.get()) {
                    emit(RealtimeVoiceEvent.Status("实时语音已结束"))
                }
                stopInternal(closeSocket = false)
            }
        })
    }

    fun stop() {
        socket?.send("{\"type\":\"stop\"}")
        stopInternal(closeSocket = true)
    }

    fun setMicrophoneEnabled(enabled: Boolean) {
        microphoneEnabled.set(enabled)
    }

    @Suppress("DEPRECATION")
    fun setSpeakerEnabled(enabled: Boolean) {
        speakerEnabled.set(enabled)
        val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        runCatching { audioManager.isSpeakerphoneOn = enabled }
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
            "audio" -> {
                val encoded = json.optString("delta")
                if (encoded.isNotBlank()) {
                    runCatching { Base64.decode(encoded, Base64.DEFAULT) }
                        .getOrNull()
                        ?.takeIf { it.isNotEmpty() }
                        ?.let(::playAudio)
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
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        runCatching { audioManager.isSpeakerphoneOn = speakerEnabled.get() }

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
            emit(RealtimeVoiceEvent.Error("麦克风初始化失败（${inputSampleRate}Hz）"))
            return
        }
        audioRecord = recorder
        if (AcousticEchoCanceler.isAvailable()) {
            echoCanceler = runCatching { AcousticEchoCanceler.create(recorder.audioSessionId) }.getOrNull()?.apply {
                enabled = true
            }
        }

        val minPlay = AudioTrack.getMinBufferSize(
            outputSampleRate,
            AudioFormat.CHANNEL_OUT_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        ).coerceAtLeast(4096)
        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(outputSampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setBufferSizeInBytes(minPlay * 2)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
            .also { it.play() }

        recorder.startRecording()
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
        }
    }

    private fun playAudio(bytes: ByteArray) {
        val track = audioTrack ?: return
        runCatching { track.write(bytes, 0, bytes.size, AudioTrack.WRITE_BLOCKING) }
    }

    @Suppress("DEPRECATION")
    private fun stopInternal(closeSocket: Boolean) {
        if (!running.getAndSet(false) && audioRecord == null && audioTrack == null) return
        runCatching { audioRecord?.stop() }
        runCatching { recordThread?.join(400) }
        echoCanceler?.release()
        echoCanceler = null
        audioRecord?.release()
        audioRecord = null
        runCatching { audioTrack?.stop() }
        audioTrack?.release()
        audioTrack = null
        val audioManager = appContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        previousSpeakerphoneOn?.let { previous -> runCatching { audioManager.isSpeakerphoneOn = previous } }
        previousSpeakerphoneOn = null
        previousAudioMode?.let { audioManager.mode = it }
        previousAudioMode = null
        if (closeSocket) socket?.close(1000, "client stop")
        socket = null
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

    private fun sanitizeSampleRate(value: Int, fallback: Int): Int =
        value.takeIf { it in 8_000..48_000 } ?: fallback

    companion object {
        private const val DEFAULT_INPUT_SAMPLE_RATE = 24_000
        private const val DEFAULT_OUTPUT_SAMPLE_RATE = 24_000
    }
}
