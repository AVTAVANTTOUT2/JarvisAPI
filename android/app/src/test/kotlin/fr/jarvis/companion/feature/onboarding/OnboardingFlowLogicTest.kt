package fr.jarvis.companion.feature.onboarding

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnboardingFlowLogicTest {
    @Test
    fun sanitizePairingCode_keepsOnlyFirstSixDigits() {
        val result = sanitizePairingCode(" 12a3-456789 ")

        assertEquals("123456", result)
    }

    @Test
    fun validatePairingCode_returnsErrorWhenNotSixDigits() {
        assertEquals("Code à six chiffres requis", validatePairingCode("12345"))
    }

    @Test
    fun validatePairingCode_returnsNullWhenValid() {
        assertEquals(null, validatePairingCode("012345"))
    }

    @Test
    fun onboardingProgress_returnsFractionForCurrentStep() {
        val progress = onboardingProgress(step = 2, stepCount = 5)

        assertEquals(0.6f, progress)
    }

    @Test
    fun buildStepperStates_marksCompletedCurrentAndUpcomingSteps() {
        val states = buildStepperStates(currentStep = 2, stepCount = 5)

        assertEquals(5, states.size)
        assertTrue(states[0].completed)
        assertTrue(states[1].completed)
        assertTrue(states[2].current)
        assertFalse(states[3].completed)
        assertFalse(states[4].current)
    }
}
