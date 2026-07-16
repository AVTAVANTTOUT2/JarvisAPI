package fr.jarvis.companion.feature.more

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import fr.jarvis.companion.navigation.JarvisDestination
import fr.jarvis.companion.ui.theme.JarvisTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MoreScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun moreScreen_showsFuturePlaceholdersAndNavigatesRealTiles() {
        var lastRoute: String? = null

        composeRule.setContent {
            JarvisTheme {
                MoreScreen(onNavigate = { route -> lastRoute = route })
            }
        }

        composeRule.onNodeWithText("Mémoire").assertExists()
        composeRule.onNodeWithText("Contacts").assertExists()
        composeRule.onNodeWithText("Automatisations").assertExists()
        composeRule.onNodeWithText("Tâches").performClick()

        assertEquals(JarvisDestination.TASKS, lastRoute)
    }
}
