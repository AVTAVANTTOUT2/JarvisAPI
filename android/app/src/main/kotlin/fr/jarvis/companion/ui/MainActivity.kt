package fr.jarvis.companion.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.notifications.JarvisNotifications
import fr.jarvis.companion.services.JarvisLocationService
import fr.jarvis.companion.services.JarvisWakeWordService
import fr.jarvis.companion.ui.theme.JarvisTheme

/** Interface native du compagnon JARVIS — aucun WebView. */
class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels()
    private var locationPendingEnable = false
    private var wakePendingEnable = false

    private val locationPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { grants ->
        val fine = grants[Manifest.permission.ACCESS_FINE_LOCATION] == true
        if (locationPendingEnable && fine) {
            requestBackgroundLocationOrStart()
        }
        locationPendingEnable = false
    }

    private val backgroundLocationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (locationPendingEnable && granted) {
            enableLocationService()
        }
        locationPendingEnable = false
    }

    private val micPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (wakePendingEnable && granted) {
            enableWakeWordService()
        }
        wakePendingEnable = false
    }

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* best effort */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        JarvisNotifications.createChannels(this)
        requestNotificationPermissionIfNeeded()
        resumePersistentFeatures()

        setContent {
            JarvisTheme {
                val state by viewModel.state.collectAsState()
                var showServerDialog by remember { mutableStateOf(false) }
                var showPairingDialog by remember { mutableStateOf(false) }
                var showPorcupineDialog by remember { mutableStateOf(false) }

                LaunchedEffect(state.phase) {
                    if (state.phase == Phase.NeedsServer) showServerDialog = true
                    if (state.phase == Phase.NeedsPairing && state.errorMessage == null) {
                        showPairingDialog = true
                    }
                }

                Scaffold { padding ->
                    CompanionScreen(
                        modifier = Modifier.padding(padding),
                        state = state,
                        onRetry = viewModel::refresh,
                        onOpenServer = { showServerDialog = true },
                        onOpenPairing = { showPairingDialog = true },
                        onClearPairing = viewModel::clearPairing,
                        onLocationToggle = { enabled -> toggleLocation(enabled) },
                        onWakeToggle = { enabled -> toggleWakeWord(enabled) },
                        onPorcupineKey = { showPorcupineDialog = true },
                    )
                }

                if (showServerDialog) {
                    ServerDialog(
                        initial = state.serverUrl,
                        onDismiss = { showServerDialog = false },
                        onSave = { url ->
                            viewModel.saveServer(
                                url,
                                onInvalid = { /* géré dans le dialog */ },
                            )
                            showServerDialog = false
                        },
                    )
                }

                if (showPairingDialog) {
                    PairingDialog(
                        onDismiss = { showPairingDialog = false },
                        onPair = { code, setError ->
                            viewModel.completePairing(code) { setError(it) }
                            showPairingDialog = false
                        },
                    )
                }

                if (showPorcupineDialog) {
                    PorcupineKeyDialog(
                        onDismiss = { showPorcupineDialog = false },
                        onSave = { key ->
                            viewModel.savePorcupineKey(key)
                            showPorcupineDialog = false
                            if (wakePendingEnable || !JarvisSettings.isWakeWordEnabled(this)) {
                                toggleWakeWord(true)
                            }
                        },
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        viewModel.refresh()
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun toggleLocation(enabled: Boolean) {
        if (!enabled) {
            viewModel.setLocationEnabled(false)
            stopService(Intent(this, JarvisLocationService::class.java))
            return
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            locationPendingEnable = true
            locationPermissions.launch(
                arrayOf(
                    Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION,
                ),
            )
            return
        }
        requestBackgroundLocationOrStart()
    }

    private fun requestBackgroundLocationOrStart() {
        locationPendingEnable = true
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R &&
            checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            startActivity(
                Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName"),
                ),
            )
            return
        }
        if (Build.VERSION.SDK_INT == Build.VERSION_CODES.Q &&
            checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            != PackageManager.PERMISSION_GRANTED
        ) {
            backgroundLocationPermission.launch(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
            return
        }
        enableLocationService()
    }

    private fun enableLocationService() {
        locationPendingEnable = false
        viewModel.setLocationEnabled(true)
        startForegroundService(Intent(this, JarvisLocationService::class.java))
    }

    private fun toggleWakeWord(enabled: Boolean) {
        if (!enabled) {
            viewModel.setWakeWordEnabled(false)
            stopService(Intent(this, JarvisWakeWordService::class.java))
            return
        }
        if (JarvisSettings.porcupineAccessKey(this).isEmpty()) {
            wakePendingEnable = true
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            wakePendingEnable = true
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        enableWakeWordService()
    }

    private fun enableWakeWordService() {
        wakePendingEnable = false
        viewModel.setWakeWordEnabled(true)
        startForegroundService(Intent(this, JarvisWakeWordService::class.java))
    }

    private fun resumePersistentFeatures() {
        if (JarvisSettings.isLocationEnabled(this) &&
            checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            startForegroundService(Intent(this, JarvisLocationService::class.java))
        }
        if (JarvisSettings.isWakeWordEnabled(this) &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED &&
            JarvisSettings.porcupineAccessKey(this).isNotEmpty()
        ) {
            startForegroundService(Intent(this, JarvisWakeWordService::class.java))
        }
    }
}

@Composable
private fun CompanionScreen(
    modifier: Modifier = Modifier,
    state: DashboardState,
    onRetry: () -> Unit,
    onOpenServer: () -> Unit,
    onOpenPairing: () -> Unit,
    onClearPairing: () -> Unit,
    onLocationToggle: (Boolean) -> Unit,
    onWakeToggle: (Boolean) -> Unit,
    onPorcupineKey: () -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("JARVIS Companion", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Text("Compagnon natif — GPS, wake word et notifications push", style = MaterialTheme.typography.bodyMedium)

        when (state.phase) {
            Phase.Loading -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 12.dp))
                    Text("Connexion au Mac…")
                }
            }
            Phase.Offline -> {
                StatusCard("Hors ligne", state.errorMessage ?: "Serveur injoignable", isError = true)
                Button(onClick = onRetry, modifier = Modifier.fillMaxWidth()) { Text("Réessayer") }
            }
            Phase.NeedsServer -> {
                StatusCard("Configuration requise", "Indiquez l'adresse HTTPS du Mac JARVIS.")
                Button(onClick = onOpenServer, modifier = Modifier.fillMaxWidth()) { Text("Adresse du serveur") }
            }
            Phase.NeedsPairing -> {
                StatusCard(
                    "Appairage requis",
                    state.errorMessage
                        ?: "Ouvrez JARVIS sur le Mac, onglet Téléphone, puis générez un code à six chiffres.",
                )
                Button(onClick = onOpenPairing, modifier = Modifier.fillMaxWidth()) { Text("Saisir le code") }
            }
            Phase.Ready -> {
                StatusCard("Connecté", "Appairé avec ${state.serverUrl}", isError = false)
            }
        }

        InfoRow("Serveur", state.serverUrl)
        InfoRow("Appareil", state.deviceId)
        InfoRow("Version", state.appVersion)

        FeatureToggle(
            title = "Présence GPS",
            subtitle = "Service de premier plan, envoi économe au Mac",
            checked = state.locationEnabled,
            enabled = state.isPaired,
            onCheckedChange = onLocationToggle,
        )

        FeatureToggle(
            title = "Mot « JARVIS » (Porcupine)",
            subtitle = if (state.hasPorcupineKey) "Clé Picovoice enregistrée" else "Clé Picovoice requise",
            checked = state.wakeWordEnabled,
            enabled = state.isPaired,
            onCheckedChange = onWakeToggle,
        )
        TextButton(onClick = onPorcupineKey, enabled = state.isPaired) {
            Text("Configurer la clé Picovoice")
        }

        StatusCard(
            title = "Notifications push (FCM)",
            body = if (state.firebaseConfigured) {
                "Firebase configuré — les jetons seront enregistrés après appairage."
            } else {
                "Désactivées dans ce build (google-services.json absent). Les autres fonctions restent actives."
            },
            isError = !state.firebaseConfigured,
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            TextButton(onClick = onOpenServer) { Text("Serveur") }
            TextButton(onClick = onOpenPairing, enabled = state.backendReachable) { Text("Appairer") }
            TextButton(onClick = onClearPairing, enabled = state.isPaired) { Text("Révoquer localement") }
        }
    }
}

@Composable
private fun StatusCard(title: String, body: String, isError: Boolean = false) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = if (isError) MaterialTheme.colorScheme.error.copy(alpha = 0.15f)
            else MaterialTheme.colorScheme.surface,
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(6.dp))
            Text(body, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Column(Modifier.fillMaxWidth()) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun FeatureToggle(
    title: String,
    subtitle: String,
    checked: Boolean,
    enabled: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, fontWeight = FontWeight.Medium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange, enabled = enabled)
    }
}

@Composable
private fun ServerDialog(
    initial: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var url by remember { mutableStateOf(initial) }
    var invalid by remember { mutableStateOf(false) }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Connexion à JARVIS") },
        text = {
            Column {
                Text("Adresse HTTPS du Mac. Certificat JARVIS intégré — émulateur : 10.0.2.2:8081")
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it; invalid = false },
                    isError = invalid,
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                if (fr.jarvis.companion.network.ServerUrlNormalizer.normalize(url) == null) {
                    invalid = true
                } else {
                    onSave(url)
                }
            }) {
                Text("Connecter")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Annuler") } },
    )
}

@Composable
private fun PairingDialog(onDismiss: () -> Unit, onPair: (String, (String) -> Unit) -> Unit) {
    var code by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Appairer ce téléphone") },
        text = {
            Column {
                Text(
                    "Interface web JARVIS → onglet Téléphone → Générer un code. " +
                        "Saisissez les six chiffres ci-dessous.",
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = code,
                    onValueChange = { code = it.filter(Char::isDigit).take(6); error = null },
                    isError = error != null,
                    supportingText = error?.let { { Text(it) } },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (code.length != 6) {
                        error = "Code à six chiffres requis"
                    } else {
                        onPair(code) { error = it }
                    }
                },
            ) { Text("Appairer") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Annuler") } },
    )
}

@Composable
private fun PorcupineKeyDialog(onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var key by remember { mutableStateOf("") }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Clé Picovoice") },
        text = {
            Column {
                Text("AccessKey gratuite depuis Picovoice Console. Reste chiffrée sur l'appareil.")
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = key,
                    onValueChange = { key = it },
                    visualTransformation = PasswordVisualTransformation(),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { if (key.isNotBlank()) onSave(key.trim()) }) { Text("Enregistrer") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Annuler") } },
    )
}
