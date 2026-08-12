package fr.jarvis.companion.feature.settings

import com.google.gson.JsonParser
import fr.jarvis.companion.network.AgenticArtifactDto
import fr.jarvis.companion.network.AgenticEventDto
import fr.jarvis.companion.network.AgenticRunDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class AgenticDisplayPolicyTest {
    @Test
    fun safeTextRedactsSecretsTokensAndLocalPaths() {
        val rendered = AgenticDisplayPolicy.safeText(
            "token=abc123 Bearer ey.secret /Users/alice/private.txt C:\\keys\\secret.txt sk-project-token",
        )

        assertFalse(rendered.contains("abc123"))
        assertFalse(rendered.contains("ey.secret"))
        assertFalse(rendered.contains("/Users/alice"))
        assertFalse(rendered.contains("C:\\keys"))
        assertFalse(rendered.contains("sk-project-token"))
        assertTrue(rendered.contains("masqué"))
    }

    @Test
    fun eventProjectionNeverUsesProviderPayloadOrToolName() {
        val event = AgenticEventDto(
            event_id = "event-1",
            run_id = "run-1",
            type = "agent.tool.started",
        )

        assertEquals("Outil interne démarré", AgenticDisplayPolicy.eventLabel(event))
    }

    @Test
    fun durationAndResultAreBoundedUserSafeSummaries() {
        val result = JsonParser.parseString(
            """{"summary":"Rapport écrit dans /Users/alice/project/output.md avec token=secret-value"}""",
        )
        val run = AgenticRunDto(
            run_id = "run-1",
            started_at = "2026-08-11T10:00:00Z",
            finished_at = "2026-08-11T10:02:05Z",
            result = result,
        )

        assertEquals(
            "2 min 5 s",
            AgenticDisplayPolicy.durationLabel(run, Instant.parse("2026-08-11T11:00:00Z")),
        )
        val summary = AgenticDisplayPolicy.resultSummary(run).orEmpty()
        assertFalse(summary.contains("/Users/alice"))
        assertFalse(summary.contains("secret-value"))
    }

    @Test
    fun artifactLabelNeverIncludesReferenceOrFilename() {
        val artifact = AgenticArtifactDto(
            artifact_id = "artifact-1",
            filename = "/Users/alice/confidential/report.md",
            type = "report",
            reference = "/Users/alice/confidential/report.md",
            size_bytes = 2048,
        )

        assertEquals("report · 2 Ko", AgenticDisplayPolicy.artifactLabel(artifact))
    }
}
