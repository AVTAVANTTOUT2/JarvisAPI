package fr.jarvis.companion.ui

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import fr.jarvis.companion.BuildConfig
import fr.jarvis.companion.data.JarvisRepository
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.network.ServerUrlNormalizer
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class DashboardState(
    val phase: Phase = Phase.Loading,
    val serverUrl: String = "",
    val serverHint: String = BuildConfig.DEFAULT_SERVER,
    val errorMessage: String? = null,
    val isPaired: Boolean = false,
    val backendReachable: Boolean = false,
    val locationEnabled: Boolean = false,
    val wakeWordEnabled: Boolean = false,
    val hasPorcupineKey: Boolean = false,
    val firebaseConfigured: Boolean = BuildConfig.FIREBASE_CONFIGURED,
    val deviceId: String = "",
    val appVersion: String = BuildConfig.VERSION_NAME,
)

enum class Phase {
    Loading,
    NeedsServer,
    NeedsPairing,
    Ready,
    Offline,
}

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application.applicationContext
    private val repository = JarvisRepository(app)

    private val _state = MutableStateFlow(DashboardState())
    val state: StateFlow<DashboardState> = _state.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            val server = JarvisSettings.server(app)
            _state.update {
                it.copy(
                    phase = Phase.Loading,
                    serverUrl = server,
                    deviceId = JarvisSettings.deviceId(app),
                    locationEnabled = JarvisSettings.isLocationEnabled(app),
                    wakeWordEnabled = JarvisSettings.isWakeWordEnabled(app),
                    hasPorcupineKey = JarvisSettings.porcupineAccessKey(app).isNotEmpty(),
                    firebaseConfigured = BuildConfig.FIREBASE_CONFIGURED,
                    errorMessage = null,
                )
            }

            if (!JarvisSettings.hasServerConfigured(app) || server.isBlank()) {
                _state.update { it.copy(phase = Phase.NeedsServer, serverHint = BuildConfig.DEFAULT_SERVER) }
                return@launch
            }

            if (!isOnline(app)) {
                _state.update {
                    it.copy(
                        phase = Phase.Offline,
                        errorMessage = "Aucun réseau détecté. Vérifie le Wi-Fi ou Tailscale.",
                    )
                }
                return@launch
            }

            val ping = repository.pingAuthStatus()
            if (!ping.ok) {
                _state.update {
                    it.copy(
                        phase = Phase.Offline,
                        backendReachable = false,
                        errorMessage = tlsHint(ping.error),
                    )
                }
                return@launch
            }

            val token = JarvisSettings.nativeToken(app)
            if (token.isEmpty()) {
                _state.update {
                    it.copy(
                        phase = Phase.NeedsPairing,
                        backendReachable = true,
                        isPaired = false,
                    )
                }
                return@launch
            }

            val session = repository.validateNativeToken()
            when {
                session.ok -> {
                    initializeFcmIfNeeded()
                    _state.update {
                        it.copy(
                            phase = Phase.Ready,
                            backendReachable = true,
                            isPaired = true,
                            errorMessage = null,
                        )
                    }
                    syncCapabilities()
                }
                session.status == 401 -> {
                    JarvisSettings.clearNativeToken(app)
                    _state.update {
                        it.copy(
                            phase = Phase.NeedsPairing,
                            isPaired = false,
                            backendReachable = true,
                            errorMessage = "Jeton révoqué ou expiré. Réappairez le téléphone.",
                        )
                    }
                }
                else -> {
                    _state.update {
                        it.copy(
                            phase = Phase.Offline,
                            backendReachable = false,
                            errorMessage = session.error,
                        )
                    }
                }
            }
        }
    }

    fun saveServer(raw: String, onInvalid: () -> Unit) {
        val normalized = ServerUrlNormalizer.normalize(raw)
        if (normalized == null) {
            onInvalid()
            return
        }
        val changed = normalized != JarvisSettings.server(app)
        JarvisSettings.setServer(app, normalized)
        if (changed) {
            JarvisSettings.clearNativeToken(app)
            repository.invalidateHttpCache()
        }
        refresh()
    }

    fun completePairing(code: String, onError: (String) -> Unit) {
        viewModelScope.launch {
            val result = repository.completePairing(code)
            when {
                result.ok -> {
                    val token = result.json.optString("token", "")
                    if (token.isNotEmpty()) {
                        JarvisSettings.setNativeToken(app, token)
                        refresh()
                    } else {
                        onError("Réponse serveur invalide")
                    }
                }
                else -> onError(result.error)
            }
        }
    }

    fun clearPairing() {
        JarvisSettings.clearNativeToken(app)
        refresh()
    }

    fun setLocationEnabled(enabled: Boolean) {
        JarvisSettings.setLocationEnabled(app, enabled)
        _state.update { it.copy(locationEnabled = enabled) }
        viewModelScope.launch { syncCapabilities() }
    }

    fun setWakeWordEnabled(enabled: Boolean) {
        JarvisSettings.setWakeWordEnabled(app, enabled)
        _state.update { it.copy(wakeWordEnabled = enabled) }
        viewModelScope.launch { syncCapabilities() }
    }

    fun savePorcupineKey(key: String) {
        JarvisSettings.setPorcupineAccessKey(app, key)
        _state.update { it.copy(hasPorcupineKey = key.isNotEmpty()) }
    }

    private suspend fun syncCapabilities() {
        val current = _state.value
        repository.updateCapabilities(current.locationEnabled, current.wakeWordEnabled)
    }

    private fun initializeFcmIfNeeded() {
        if (!BuildConfig.FIREBASE_CONFIGURED) return
        if (FirebaseApp.initializeApp(app) == null) return
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            if (!token.isNullOrEmpty()) {
                viewModelScope.launch { repository.registerPushToken(token) }
            }
        }
    }

    private fun tlsHint(error: String): String {
        val base = error.ifBlank { "Connexion impossible" }
        return "$base\n\nVérifiez que JARVIS tourne en HTTPS (WEB_HTTPS=true) " +
            "et que l'adresse correspond à l'émulateur (10.0.2.2) ou Tailscale."
    }

    companion object {
        fun isOnline(context: Context): Boolean {
            val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return false
            val network = manager.activeNetwork ?: return false
            val caps = manager.getNetworkCapabilities(network) ?: return false
            return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        }
    }
}
