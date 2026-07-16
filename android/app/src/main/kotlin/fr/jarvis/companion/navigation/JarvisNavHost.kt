package fr.jarvis.companion.navigation

import android.content.Intent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import fr.jarvis.companion.app.appContainer
import fr.jarvis.companion.core.ui.components.JarvisBackground
import fr.jarvis.companion.data.JarvisSettings
import fr.jarvis.companion.feature.agenda.AgendaScreen
import fr.jarvis.companion.feature.agenda.AgendaViewModel
import fr.jarvis.companion.feature.agenda.AgendaViewModelFactory
import fr.jarvis.companion.feature.chat.ChatScreen
import fr.jarvis.companion.feature.chat.ChatViewModel
import fr.jarvis.companion.feature.chat.ChatViewModelFactory
import fr.jarvis.companion.feature.chat.ConversationListScreen
import fr.jarvis.companion.feature.chat.ConversationListViewModel
import fr.jarvis.companion.feature.chat.ConversationListViewModelFactory
import fr.jarvis.companion.feature.diagnostics.DiagnosticsScreen
import fr.jarvis.companion.feature.home.HomeScreen
import fr.jarvis.companion.feature.home.HomeViewModel
import fr.jarvis.companion.feature.home.HomeViewModelFactory
import fr.jarvis.companion.feature.location.LocationScreen
import fr.jarvis.companion.feature.more.MoreScreen
import fr.jarvis.companion.feature.notifications.NotificationsScreen
import fr.jarvis.companion.feature.repair.RepairScreen
import fr.jarvis.companion.feature.settings.SettingsScreen
import fr.jarvis.companion.feature.tasks.TasksScreen
import fr.jarvis.companion.feature.tasks.TasksViewModel
import fr.jarvis.companion.feature.tasks.TasksViewModelFactory
import fr.jarvis.companion.ui.theme.rememberReducedMotion
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
    val reducedMotion = rememberReducedMotion()

    var locationEnabled by remember { mutableStateOf(JarvisSettings.isLocationEnabled(context)) }
    var wakeEnabled by remember { mutableStateOf(JarvisSettings.isWakeWordEnabled(context)) }
    var hasPorcupineKey by remember {
        mutableStateOf(JarvisSettings.porcupineAccessKey(context).isNotEmpty())
    }

    val showBottomBar = currentRoute in JarvisDestination.bottomBarRoutes ||
        currentRoute == JarvisDestination.HOME

    val navigate: (String) -> Unit = { route ->
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
    }

    JarvisBackground {
        Scaffold(
            modifier = modifier,
            containerColor = Color.Transparent,
            bottomBar = {
                if (!useRail && showBottomBar) {
                    JarvisBottomBar(
                        currentRoute = currentRoute,
                        onNavigate = navigate,
                    )
                }
            },
        ) { padding ->
            Row(Modifier.padding(padding)) {
                if (useRail && showBottomBar) {
                    JarvisNavRail(
                        currentRoute = currentRoute,
                        onNavigate = navigate,
                    )
                }
                NavHost(
                    navController = navController,
                    startDestination = JarvisDestination.HOME,
                    modifier = Modifier.weight(1f),
                    enterTransition = {
                        if (reducedMotion) {
                            fadeIn(tween(0))
                        } else {
                            fadeIn(tween(180)) + slideInVertically(tween(180)) { it / 40 }
                        }
                    },
                    exitTransition = { fadeOut(tween(if (reducedMotion) 0 else 120)) },
                    popEnterTransition = { fadeIn(tween(if (reducedMotion) 0 else 180)) },
                    popExitTransition = { fadeOut(tween(if (reducedMotion) 0 else 120)) },
                ) {
                    composable(JarvisDestination.HOME) {
                        val homeViewModel: HomeViewModel = viewModel(
                            factory = HomeViewModelFactory(container),
                        )
                        HomeScreen(
                            viewModel = homeViewModel,
                            onOpenChat = { navigate(JarvisDestination.CHAT) },
                            onOpenVoice = { navigate(JarvisDestination.VOICE) },
                        )
                    }
                    composable(JarvisDestination.CHAT) {
                        val listViewModel: ConversationListViewModel = viewModel(
                            factory = ConversationListViewModelFactory(container),
                        )
                        ConversationListScreen(
                            viewModel = listViewModel,
                            onOpenChat = { localId ->
                                navController.navigate(JarvisDestination.chatDetail(localId))
                            },
                        )
                    }
                    composable(
                        route = JarvisDestination.CHAT_DETAIL,
                        arguments = listOf(navArgument("localId") { type = NavType.LongType }),
                    ) { entry ->
                        val localId = entry.arguments?.getLong("localId") ?: return@composable
                        val chatViewModel: ChatViewModel = viewModel(
                            factory = ChatViewModelFactory(container, localId),
                        )
                        ChatScreen(
                            viewModel = chatViewModel,
                            onBack = { navController.popBackStack() },
                        )
                    }
                    composable(JarvisDestination.CALENDAR) {
                        val agendaViewModel: AgendaViewModel = viewModel(
                            factory = AgendaViewModelFactory(container),
                        )
                        AgendaScreen(viewModel = agendaViewModel)
                    }
                    composable(JarvisDestination.MORE) {
                        MoreScreen(
                            onNavigate = { route ->
                                navController.navigate(route) { launchSingleTop = true }
                            },
                        )
                    }
                    composable(JarvisDestination.TASKS) {
                        val tasksViewModel: TasksViewModel = viewModel(
                            factory = TasksViewModelFactory(container),
                        )
                        TasksScreen(viewModel = tasksViewModel)
                    }
                    composable(JarvisDestination.LOCATION) {
                        LocationScreen()
                    }
                    composable(JarvisDestination.NOTIFICATIONS) {
                        NotificationsScreen()
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
}
