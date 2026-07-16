package fr.jarvis.companion.feature.repair

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RepairPresentationLogicTest {
    @Test
    fun buildRepairActions_containsOnlyConfirmedDangerOperations() {
        val actions = buildRepairActions()

        assertEquals(2, actions.size)
        assertTrue(actions.all { it.requiresConfirmation })
        assertTrue(actions.all { it.dangerZone })
    }

    @Test
    fun revokeTokenAction_keepsScopeLimitedToLocalToken() {
        val revokeAction = buildRepairActions().first { it.type == RepairActionType.RevokeToken }

        assertEquals("Révoquer le jeton local", revokeAction.ctaLabel)
        assertTrue(revokeAction.description.contains("ce téléphone"))
    }
}
