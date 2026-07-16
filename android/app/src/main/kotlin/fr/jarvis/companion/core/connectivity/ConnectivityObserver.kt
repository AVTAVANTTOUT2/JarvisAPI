package fr.jarvis.companion.core.connectivity

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class ConnectivityState {
    Offline,
    NetworkAvailable,
    ServerReachable,
    Unauthorized,
}

/** Observe la connectivité réseau et l'état serveur mis à jour par SyncManager. */
class ConnectivityObserver(context: Context) {
    private val appContext = context.applicationContext
    private val connectivityManager =
        appContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    private var networkUp = false
    private var serverReachable = false
    private var unauthorized = false
    private var started = false

    private val _state = MutableStateFlow(ConnectivityState.Offline)
    val state: StateFlow<ConnectivityState> = _state.asStateFlow()

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            networkUp = true
            emitCombined()
        }

        override fun onLost(network: Network) {
            if (!hasInternetNetwork()) {
                networkUp = false
                serverReachable = false
                unauthorized = false
                emitCombined()
            }
        }

        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            val hasInternet = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
            networkUp = hasInternet
            if (!networkUp) {
                serverReachable = false
                unauthorized = false
            }
            emitCombined()
        }
    }

    fun start() {
        if (started) return
        started = true
        networkUp = hasInternetNetwork()
        emitCombined()
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        connectivityManager.registerNetworkCallback(request, callback)
    }

    fun stop() {
        if (!started) return
        started = false
        runCatching { connectivityManager.unregisterNetworkCallback(callback) }
    }

    fun reportServerReachable() {
        unauthorized = false
        serverReachable = true
        emitCombined()
    }

    fun reportUnauthorized() {
        unauthorized = true
        serverReachable = false
        emitCombined()
    }

    fun reportServerUnreachable() {
        serverReachable = false
        unauthorized = false
        emitCombined()
    }

    fun resetServerState() {
        serverReachable = false
        unauthorized = false
        emitCombined()
    }

    private fun emitCombined() {
        _state.value = when {
            !networkUp -> ConnectivityState.Offline
            unauthorized -> ConnectivityState.Unauthorized
            serverReachable -> ConnectivityState.ServerReachable
            else -> ConnectivityState.NetworkAvailable
        }
    }

    private fun hasInternetNetwork(): Boolean {
        val network = connectivityManager.activeNetwork ?: return false
        val caps = connectivityManager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
