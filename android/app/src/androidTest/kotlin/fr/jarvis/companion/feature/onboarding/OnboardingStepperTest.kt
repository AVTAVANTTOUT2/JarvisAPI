package fr.jarvis.companion.feature.onboarding

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.test.ext.junit.runners.AndroidJUnit4
import fr.jarvis.companion.ui.theme.JarvisTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OnboardingStepperTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun onboardingStepper_exposesAccessibleProgressDescription() {
        composeRule.setContent {
            JarvisTheme {
                OnboardingStepper(
                    currentStep = 2,
                    stepCount = 5,
                )
            }
        }

        composeRule
            .onNodeWithContentDescription("Progression onboarding étape 3 sur 5")
            .assertExists()
    }
}
