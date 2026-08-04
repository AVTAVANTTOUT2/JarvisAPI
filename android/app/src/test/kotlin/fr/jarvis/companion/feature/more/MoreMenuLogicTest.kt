package fr.jarvis.companion.feature.more

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MoreMenuLogicTest {
    @Test
    fun buildMoreMenuTiles_containsExpectedRealRoutes() {
        val tiles = buildMoreMenuTiles()
        val realTiles = tiles.filter { it.kind == MoreTileKind.RealRoute }

        assertEquals(6, realTiles.size)
        assertTrue(realTiles.all { it.route != null && it.route.isNotBlank() })
        assertTrue(realTiles.none { it.backlogId != null })
    }

    @Test
    fun buildMoreMenuTiles_exposesFuturePlaceholdersAsInertTiles() {
        val tiles = buildMoreMenuTiles()
        val futureTiles = tiles.filter { it.kind == MoreTileKind.FuturePlaceholder }

        assertEquals(3, futureTiles.size)
        assertTrue(futureTiles.all { it.route == null })
        assertTrue(futureTiles.all { it.backlogId != null })
    }

    @Test
    fun toAccessibilityHint_describesInertTile() {
        val tile = MoreTileModel(
            title = "Mémoire",
            subtitle = "Vue personnelle JARVIS",
            kind = MoreTileKind.FuturePlaceholder,
            route = null,
            backlogId = "JARVIS-FUTURE-MEMORY-VIEW",
        )

        assertEquals("Mémoire, bientôt disponible", tile.toAccessibilityHint())
        assertFalse(tile.isNavigable())
    }
}
