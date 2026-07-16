package fr.jarvis.companion.core.location

import android.util.Log
import fr.jarvis.companion.BuildConfig
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Diagnostics runtime localisation — sans coordonnées.
 * Remplace le faux « OK » par une chaîne d'état vérifiable.
 */
object LocationRuntimeDiagnostics {
    private const val TAG = "JarvisLocation"

    val serviceRunning = AtomicBoolean(false)
    val engineStarted = AtomicBoolean(false)
    val gpsProviderEnabled = AtomicBoolean(false)
    val networkProviderEnabled = AtomicBoolean(false)

    val lastCallbackAt = AtomicLong(0L)
    val lastAcceptedAt = AtomicLong(0L)
    val lastRejectedAt = AtomicLong(0L)
    val lastInsertAt = AtomicLong(0L)
    val lastSyncRequestAt = AtomicLong(0L)
    val lastHttpStatus = AtomicInteger(0)
    val lastBatchAccepted = AtomicInteger(0)
    val lastRejectReason = AtomicReference<String?>(null)
    val lastStatusLine = AtomicReference("Inactif")

    fun logDebug(message: String) {
        if (BuildConfig.DEBUG) {
            Log.d(TAG, message)
        }
    }

    fun logInfo(message: String) {
        Log.i(TAG, message)
    }

    fun logWarn(message: String) {
        Log.w(TAG, message)
    }

    fun onCallback() {
        lastCallbackAt.set(System.currentTimeMillis())
        logDebug("Location callback received")
    }

    fun onRejected(reason: String) {
        lastRejectedAt.set(System.currentTimeMillis())
        lastRejectReason.set(reason)
        logDebug("Location accepted/rejected: $reason")
    }

    fun onAccepted() {
        lastAcceptedAt.set(System.currentTimeMillis())
        logDebug("Location accepted/rejected: accepted")
    }

    fun onInserted(clientPointId: String) {
        lastInsertAt.set(System.currentTimeMillis())
        logDebug("Location inserted locally: $clientPointId")
    }

    fun onSyncRequested() {
        lastSyncRequestAt.set(System.currentTimeMillis())
        logDebug("Location sync requested")
    }

    fun onBatchReserved(count: Int) {
        logDebug("Location batch reserved: count=$count")
    }

    fun onBatchResponse(accepted: Int, duplicates: Int, rejected: Int, httpStatus: Int) {
        lastHttpStatus.set(httpStatus)
        lastBatchAccepted.set(accepted)
        logDebug(
            "Location batch response: accepted=$accepted duplicates=$duplicates rejected=$rejected http=$httpStatus",
        )
    }

    fun buildUserStatus(
        collectionEnabled: Boolean,
        finePermission: Boolean,
        pendingCount: Int,
        sendingCount: Int,
        unauthorized: Boolean,
        providersDisabled: Boolean,
    ): String {
        if (!collectionEnabled) {
            return "Collecte désactivée"
        }
        if (!finePermission) {
            return "Permission localisation manquante"
        }
        if (unauthorized) {
            return "Token révoqué — réappairage requis"
        }
        if (providersDisabled) {
            return "Localisation système désactivée — ouvrez les réglages Android"
        }
        if (!serviceRunning.get()) {
            return "Collecte activée — service arrêté (relancez)"
        }
        val now = System.currentTimeMillis()
        val lastCb = lastCallbackAt.get()
        val lastIns = lastInsertAt.get()
        val lastAcc = lastAcceptedAt.get()
        when {
            lastCb == 0L -> {
                return "En attente du GPS — aucune position reçue"
            }
            lastIns == 0L && lastRejectedAt.get() > 0L -> {
                val reason = lastRejectReason.get() ?: "rejet"
                return "Position reçue puis rejetée ($reason)"
            }
            lastIns == 0L -> {
                return "En attente du GPS — aucune position validée"
            }
            pendingCount > 0 -> {
                return "$pendingCount position(s) stockée(s) hors ligne"
            }
            sendingCount > 0 -> {
                return "Envoi en cours ($sendingCount)"
            }
            lastAcc > 0L -> {
                val ageSec = ((now - lastAcc) / 1000L).coerceAtLeast(0)
                return "Actif — dernière position reçue il y a ${ageSec}s"
            }
            else -> {
                return "Position capturée — en attente de synchronisation"
            }
        }.also { lastStatusLine.set(it) }
    }
}
