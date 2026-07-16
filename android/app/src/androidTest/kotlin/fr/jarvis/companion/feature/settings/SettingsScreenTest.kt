package fr.jarvis.companion.feature.settings

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import fr.jarvis.companion.ui.theme.JarvisTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SettingsScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun settingsScreen_displaysExpectedSectionCards() {
        composeRule.setContent {
            JarvisTheme {
                SettingsScreen(
                    locationEnabled = true,
                    wakeEnabled = false,
                    hasPorcupineKey = false,
                    onLocationToggle = {},
                    onWakeToggle = {},
                    onPorcupineKeySave = {},
                )
            }
        }

        composeRule.onNodeWithText("Connexion").assertExists()
        composeRule.onNodeWithText("Voix").assertExists()
        composeRule.onNodeWithText("Localisation").assertExists()
        composeRule.onNodeWithText("Notifications").assertExists()
        composeRule.onNodeWithText("Données").assertExists()
        composeRule.onNodeWithText("Sécurité & apparence").assertExists()
        composeRule.onNodeWithText("À propos").assertExists()
    }
}
