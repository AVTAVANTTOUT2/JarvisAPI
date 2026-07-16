package fr.jarvis.companion.feature.repair

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import fr.jarvis.companion.ui.theme.JarvisTheme
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RepairScreenTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun relaunchOnboarding_requiresConfirmationBeforeCallback() {
        var onboardingRequested = false
        composeRule.setContent {
            JarvisTheme {
                RepairScreen(onNeedsOnboarding = { onboardingRequested = true })
            }
        }

        composeRule.onNodeWithText("Relancer l'onboarding").performClick()
        assertFalse(onboardingRequested)
        composeRule.onNodeWithText("Confirmer la relance de l'onboarding ?").assertExists()
        composeRule.onNodeWithText("Confirmer").performClick()

        assertTrue(onboardingRequested)
    }

    @Test
    fun revokeToken_showsExplicitDangerConfirmationDialog() {
        composeRule.setContent {
            JarvisTheme {
                RepairScreen(onNeedsOnboarding = {})
            }
        }

        composeRule.onNodeWithText("Révoquer le jeton local").performClick()
        composeRule.onNodeWithText("Confirmer la révocation du jeton local ?").assertExists()
        composeRule.onNodeWithText("Les données locales restent intactes.").assertExists()
        composeRule.onNodeWithText("Annuler").performClick()
        composeRule.onNodeWithText("Confirmer la révocation du jeton local ?").assertDoesNotExist()
    }
}
