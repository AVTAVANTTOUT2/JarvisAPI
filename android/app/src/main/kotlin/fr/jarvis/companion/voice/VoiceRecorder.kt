package fr.jarvis.companion.voice

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** Capture push-to-talk mono AAC/M4A — nettoyage automatique du fichier temporaire. */
class VoiceRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null
    private val recording = AtomicBoolean(false)

    val isRecording: Boolean
        get() = recording.get()

    fun start(): File {
        stopInternal(deleteFile = true)
        val file = File.createTempFile("jarvis_voice_", ".m4a", context.cacheDir)
        val mediaRecorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }
        mediaRecorder.setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
        mediaRecorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        mediaRecorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
        mediaRecorder.setAudioSamplingRate(SAMPLE_RATE_HZ)
        mediaRecorder.setAudioChannels(1)
        mediaRecorder.setAudioEncodingBitRate(BIT_RATE)
        mediaRecorder.setOutputFile(file.absolutePath)
        mediaRecorder.prepare()
        mediaRecorder.start()
        recorder = mediaRecorder
        outputFile = file
        recording.set(true)
        return file
    }

    fun stop(): File? {
        if (!recording.get()) return outputFile
        stopInternal(deleteFile = false)
        return outputFile
    }

    fun cancel() {
        stopInternal(deleteFile = true)
    }

    fun deleteOutput() {
        outputFile?.delete()
        outputFile = null
    }

    private fun stopInternal(deleteFile: Boolean) {
        recording.set(false)
        recorder?.runCatching {
            stop()
            release()
        }
        recorder = null
        if (deleteFile) {
            outputFile?.delete()
            outputFile = null
        }
    }

    companion object {
        private const val SAMPLE_RATE_HZ = 16_000
        private const val BIT_RATE = 64_000
    }
}
