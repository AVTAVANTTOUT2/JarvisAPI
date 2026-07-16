package fr.jarvis.companion.navigation

import android.content.Intent
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.feature.diagnostics.DiagnosticsScreen
import fr.jarvis.companion.feature.home.HomeScreen
import fr.jarvis.companion.feature.home.HomeViewModel
import fr.jarvis.companion.feature.home.HomeViewModelFactory
import fr.jarvis.companion.feature.more.MoreScreen
import fr.jarvis.companion.feature.placeholder.PlaceholderScreen
import fr.jarvis.companion.feature.repair.RepairScreen
import fr.jarvis.companion.feature.settings.SettingsScreen
import fr.jarvis.companion.voice.VoiceActivity

data class JarvisNavCallbacks(
    val onLocationToggle: (Boolean) -> Unit,
    val onWakeToggle: (Boolean) -> Unit,
    val onPorcupineKeySave: (String) -> Unit,
    val onNeedsOnboarding: () -> Unit,
)

@Composable
fun JarvisNavHost(
    callbacks: JarvisNavCallbacks,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val container = context.appContainer()
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route ?: JarvisDestination.HOME
    val useRail = LocalConfiguration.current.screenWidthDp >= 840

    var locationEnabled by remember { mutableStateOf(JarvisSettings.isLocationEnabled(context)) }
    var wakeEnabled by remember { mutableStateOf(JarvisSettings.isWakeWordEnabled(context)) }
    var hasPorcupineKey by remember {
        mutableStateOf(JarvisSettings.porcupineAccessKey(context).isNotEmpty())
    }

    val showBottomBar = currentRoute in JarvisDestination.bottomBarRoutes ||
        currentRoute == JarvisDestination.HOME

    Scaffold(
        modifier = modifier,
        bottomBar = {
            if (!useRail && showBottomBar) {
                JarvisBottomBar(
                    currentRoute = currentRoute,
                    onNavigate = { route ->
                        if (route == JarvisDestination.VOICE) {
                            context.startActivity(Intent(context, VoiceActivity::class.java))
                        } else {
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        }
                    },
                )
            }
        },
    ) { padding ->
        androidx.compose.foundation.layout.Row(Modifier.padding(padding)) {
            if (useRail && showBottomBar) {
                JarvisNavigationRail(
                    currentRoute = currentRoute,
                    onNavigate = { route ->
                        if (route == JarvisDestination.VOICE) {
                            context.startActivity(Intent(context, VoiceActivity::class.java))
                        } else {
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) {
                                    saveState = true
                                }
                                launchSingleTop = true
                                restoreState = true
                            }
                        }
                    },
                )
            }
            NavHost(
                navController = navController,
                startDestination = JarvisDestination.HOME,
                modifier = Modifier.weight(1f),
            ) {
                composable(JarvisDestination.HOME) {
                    val homeViewModel: HomeViewModel = viewModel(
                        factory = HomeViewModelFactory(container),
                    )
                    HomeScreen(viewModel = homeViewModel)
                }
                composable(JarvisDestination.CHAT) {
                    PlaceholderScreen(
                        title = "Chat",
                        description = "Le chat texte arrive dans une prochaine vague. Utilisez la conversation vocale pour l'instant.",
                    )
                }
                composable(JarvisDestination.CALENDAR) {
                    PlaceholderScreen(
                        title = "Agenda",
                        description = "Vue agenda détaillée à venir. Les prochains événements sont visibles sur l'accueil.",
                    )
                }
                composable(JarvisDestination.MORE) {
                    MoreScreen(
                        onNavigate = { route ->
                            navController.navigate(route) { launchSingleTop = true }
                        },
                    )
                }
                composable(JarvisDestination.TASKS) {
                    PlaceholderScreen(
                        title = "Tâches",
                        description = "Liste complète des tâches — prochaine itération. L'accueil affiche les tâches ouvertes synchronisées.",
                    )
                }
                composable(JarvisDestination.LOCATION) {
                    PlaceholderScreen(
                        title = "Localisation",
                        description = "Activez le GPS dans Réglages. Les positions en attente sont visibles dans Diagnostics.",
                    )
                }
                composable(JarvisDestination.NOTIFICATIONS) {
                    PlaceholderScreen(
                        title = "Notifications",
                        description = "Historique complet à venir. Les non lues apparaissent sur l'accueil après synchronisation.",
                    )
                }
                composable(JarvisDestination.DIAGNOSTICS) {
                    DiagnosticsScreen()
                }
                composable(JarvisDestination.SETTINGS) {
                    SettingsScreen(
                        locationEnabled = locationEnabled,
                        wakeEnabled = wakeEnabled,
                        hasPorcupineKey = hasPorcupineKey,
                        onLocationToggle = { enabled ->
                            locationEnabled = enabled
                            callbacks.onLocationToggle(enabled)
                        },
                        onWakeToggle = { enabled ->
                            wakeEnabled = enabled
                            callbacks.onWakeToggle(enabled)
                        },
                        onPorcupineKeySave = { key ->
                            JarvisSettings.setPorcupineAccessKey(context, key)
                            hasPorcupineKey = key.isNotEmpty()
                            callbacks.onPorcupineKeySave(key)
                        },
                    )
                }
                composable(JarvisDestination.REPAIR) {
                    RepairScreen(onNeedsOnboarding = callbacks.onNeedsOnboarding)
                }
            }
        }
    }
}

@Composable
private fun JarvisBottomBar(
    currentRoute: String,
    onNavigate: (String) -> Unit,
) {
    NavigationBar {
        NavigationBarItem(
            selected = currentRoute == JarvisDestination.HOME,
            onClick = { onNavigate(JarvisDestination.HOME) },
            icon = { Icon(Icons.Default.Home, contentDescription = "Accueil") },
            label = { Text("Accueil") },
        )
        NavigationBarItem(
            selected = currentRoute == JarvisDestination.CHAT,
            onClick = { onNavigate(JarvisDestination.CHAT) },
            icon = { Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = "Chat") },
            label = { Text("Chat") },
        )
        NavigationBarItem(
            selected = false,
            onClick = { onNavigate(JarvisDestination.VOICE) },
            icon = { Icon(Icons.Default.Mic, contentDescription = "Voix") },
            label = { Text("Voix") },
        )
        NavigationBarItem(
            selected = currentRoute == JarvisDestination.CALENDAR,
            onClick = { onNavigate(JarvisDestination.CALENDAR) },
            icon = { Icon(Icons.Default.CalendarMonth, contentDescription = "Agenda") },
            label = { Text("Agenda") },
        )
        NavigationBarItem(
            selected = currentRoute == JarvisDestination.MORE,
            onClick = { onNavigate(JarvisDestination.MORE) },
            icon = { Icon(Icons.Default.MoreHoriz, contentDescription = "Plus") },
            label = { Text("Plus") },
        )
    }
}

@Composable
private fun JarvisNavigationRail(
    currentRoute: String,
    onNavigate: (String) -> Unit,
) {
    NavigationRail {
        NavigationRailItem(
            selected = currentRoute == JarvisDestination.HOME,
            onClick = { onNavigate(JarvisDestination.HOME) },
            icon = { Icon(Icons.Default.Home, contentDescription = "Accueil") },
            label = { Text("Accueil") },
        )
        NavigationRailItem(
            selected = currentRoute == JarvisDestination.CHAT,
            onClick = { onNavigate(JarvisDestination.CHAT) },
            icon = { Icon(Icons.AutoMirrored.Filled.Chat, contentDescription = "Chat") },
            label = { Text("Chat") },
        )
        NavigationRailItem(
            selected = false,
            onClick = { onNavigate(JarvisDestination.VOICE) },
            icon = { Icon(Icons.Default.Mic, contentDescription = "Voix") },
            label = { Text("Voix") },
        )
        NavigationRailItem(
            selected = currentRoute == JarvisDestination.CALENDAR,
            onClick = { onNavigate(JarvisDestination.CALENDAR) },
            icon = { Icon(Icons.Default.CalendarMonth, contentDescription = "Agenda") },
            label = { Text("Agenda") },
        )
        NavigationRailItem(
            selected = currentRoute == JarvisDestination.MORE,
            onClick = { onNavigate(JarvisDestination.MORE) },
            icon = { Icon(Icons.Default.MoreHoriz, contentDescription = "Plus") },
            label = { Text("Plus") },
        )
    }
}
