package fr.jarvis.companion.voice

import android.content.Context
import android.media.MediaPlayer
import android.util.Base64
import java.io.File
import java.util.concurrent.atomic.AtomicReference

/** Lecture WAV/M4A/MP3 reçus en base64 — fichier temporaire nettoyé à la fin. */
class VoicePlayer(private val context: Context) {
    private val playerRef = AtomicReference<MediaPlayer?>(null)
    private var tempFile: File? = null

    var isPlaying: Boolean
        get() = playerRef.get()?.isPlaying == true
        private set(_) {}

    fun playBase64(base64: String, mimeType: String?, onComplete: () -> Unit, onError: (String) -> Unit) {
        stop()
        val bytes = runCatching { Base64.decode(base64, Base64.DEFAULT) }.getOrElse {
            onError("Audio illisible")
            return
        }
        val ext = when {
            mimeType?.contains("wav", ignoreCase = true) == true -> ".wav"
            mimeType?.contains("mpeg", ignoreCase = true) == true -> ".mp3"
            else -> ".m4a"
        }
        val file = File.createTempFile("jarvis_reply_", ext, context.cacheDir)
        file.writeBytes(bytes)
        tempFile = file
        val player = MediaPlayer()
        playerRef.set(player)
        player.setDataSource(file.absolutePath)
        player.setOnCompletionListener {
            cleanup()
            onComplete()
        }
        player.setOnErrorListener { _, what, extra ->
            cleanup()
            onError("Lecture impossible ($what/$extra)")
            true
        }
        runCatching {
            player.prepare()
            player.start()
        }.onFailure {
            cleanup()
            onError(it.message ?: "Lecture impossible")
        }
    }

    fun stop() {
        playerRef.getAndSet(null)?.runCatching {
            if (isPlaying) stop()
            release()
        }
        cleanup()
    }

    private fun cleanup() {
        tempFile?.delete()
        tempFile = null
    }
}
