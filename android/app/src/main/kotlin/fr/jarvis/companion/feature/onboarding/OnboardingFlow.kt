package fr.jarvis.companion.feature.onboarding

data class OnboardingStepperState(
    val index: Int,
    val completed: Boolean,
    val current: Boolean,
)

fun sanitizePairingCode(raw: String): String = raw.filter(Char::isDigit).take(6)

fun validatePairingCode(code: String): String? = if (code.length == 6) {
    null
} else {
    "Code à six chiffres requis"
}

fun onboardingProgress(step: Int, stepCount: Int): Float {
    val safeCount = stepCount.coerceAtLeast(1)
    val safeStep = step.coerceIn(0, safeCount - 1)
    return (safeStep + 1f) / safeCount.toFloat()
}

fun buildStepperStates(currentStep: Int, stepCount: Int): List<OnboardingStepperState> {
    val safeCount = stepCount.coerceAtLeast(1)
    val safeStep = currentStep.coerceIn(0, safeCount - 1)
    return List(safeCount) { index ->
        OnboardingStepperState(
            index = index,
            completed = index < safeStep,
            current = index == safeStep,
        )
    }
}
