package fr.jarvis.companion.feature.settings

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import fr.jarvis.companion.network.AgenticArtifactDto
import fr.jarvis.companion.network.AgenticEventDto
import fr.jarvis.companion.network.AgenticRunDto
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

/**
 * Privacy boundary for native agentic views.
 *
 * Only explicit user-facing summary fields are accepted. Event payloads, prompts, tool names,
 * arguments and artifact references never cross this projection.
 */
internal object AgenticDisplayPolicy {
    private const val MAX_TEXT_LENGTH = 240
    private val secretAssignment = Regex(
        """\b(api[_-]?key|token|secret|password|authorization|cookie)\b\s*[:=]\s*[^\s,;]+""",
        RegexOption.IGNORE_CASE,
    )
    private val bearerToken = Regex("""\bBearer\s+[A-Za-z0-9._~+/=-]+""", RegexOption.IGNORE_CASE)
    private val providerToken = Regex("""\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{8,}\b""")
    private val unixPath = Regex("""(?<![:\p{L}\p{N}_])(?:~|/)(?:[^\s,;]+)""")
    private val windowsPath = Regex("""\b[A-Za-z]:\\[^\s,;]+""")
    private val controls = Regex("""[\p{Cc}\p{Cf}]+""")

    fun safeText(raw: String?, fallback: String = "Information masquée"): String {
        val candidate = raw?.trim().orEmpty()
        if (candidate.isEmpty()) return fallback
        val sanitized = controls.replace(candidate, " ")
            .let { secretAssignment.replace(it, "[secret masqué]") }
            .let { bearerToken.replace(it, "[secret masqué]") }
            .let { providerToken.replace(it, "[secret masqué]") }
            .let { windowsPath.replace(it, "[chemin masqué]") }
            .let { unixPath.replace(it, "[chemin masqué]") }
            .replace(Regex("""\s+"""), " ")
            .trim()
        return sanitized.take(MAX_TEXT_LENGTH).ifEmpty { fallback }
    }

    fun durationLabel(run: AgenticRunDto, now: Instant = Instant.now()): String {
        val start = parseInstant(run.started_at ?: run.created_at) ?: return "Durée indisponible"
        val end = parseInstant(run.finished_at ?: run.completed_at) ?: now
        val seconds = Duration.between(start, end).seconds.coerceAtLeast(0)
        val hours = seconds / 3_600
        val minutes = (seconds % 3_600) / 60
        val remainder = seconds % 60
        return when {
            hours > 0 -> "${hours} h ${minutes} min"
            minutes > 0 -> "${minutes} min ${remainder} s"
            else -> "${remainder} s"
        }
    }

    fun eventLabel(event: AgenticEventDto): String = when (event.type) {
        "agent.run.created" -> "Tâche créée"
        "agent.run.started" -> "Exécution démarrée"
        "agent.run.phase_changed" -> "Phase mise à jour"
        "agent.step.started" -> "Étape démarrée"
        "agent.step.completed" -> "Étape terminée"
        "agent.tool.started" -> "Outil interne démarré"
        "agent.tool.completed" -> "Outil interne terminé"
        "agent.approval.requested" -> "Autorisation demandée"
        "agent.approval.resolved" -> "Autorisation traitée"
        "agent.run.paused" -> "Tâche mise en pause"
        "agent.run.resumed" -> "Tâche reprise"
        "agent.run.verifying" -> "Résultat en vérification"
        "agent.run.completed" -> "Tâche terminée"
        "agent.run.failed" -> "Échec de la tâche"
        "agent.run.cancelled" -> "Tâche annulée"
        else -> "Activité de la tâche"
    }

    fun planItems(run: AgenticRunDto, events: List<AgenticEventDto>): List<String> {
        val explicit = extractSummaryItems(run.plan)
        if (explicit.isNotEmpty()) return explicit.take(12)
        return events.asSequence()
            .filter { it.type in setOf("agent.run.phase_changed", "agent.step.started", "agent.step.completed") }
            .map(::eventLabel)
            .distinct()
            .take(12)
            .toList()
    }

    fun resultSummary(run: AgenticRunDto): String? =
        extractSummaryItems(run.result).firstOrNull()
            ?: extractSummaryItems(run.verification).firstOrNull()

    fun artifactLabel(artifact: AgenticArtifactDto): String {
        val type = safeText(artifact.kind ?: artifact.type ?: artifact.mime_type, "artefact")
        val size = artifact.size_bytes?.let(::formatBytes)
        return listOfNotNull(type, size).joinToString(" · ")
    }

    private fun extractSummaryItems(value: JsonElement?): List<String> {
        if (value == null || value.isJsonNull) return emptyList()
        if (value.isJsonPrimitive && value.asJsonPrimitive.isString) {
            return listOf(safeText(value.asString))
        }
        if (value.isJsonArray) {
            return value.asJsonArray.flatMap(::extractSummaryItems)
        }
        if (!value.isJsonObject) return emptyList()
        val objectValue = value.asJsonObject
        val direct = listOf("title", "name", "summary", "message", "text", "content", "phase")
            .mapNotNull { key -> safeStringAt(objectValue, key) }
        val nested = listOf("steps", "items", "phases")
            .flatMap { key -> extractSummaryItems(objectValue.get(key)) }
        return (direct + nested).distinct()
    }

    private fun safeStringAt(value: JsonObject, key: String): String? {
        val element = value.get(key) ?: return null
        if (!element.isJsonPrimitive || !element.asJsonPrimitive.isString) return null
        return safeText(element.asString)
    }

    private fun parseInstant(value: String?): Instant? {
        if (value.isNullOrBlank()) return null
        return runCatching { Instant.parse(value) }.getOrNull()
            ?: runCatching { OffsetDateTime.parse(value).toInstant() }.getOrNull()
    }

    private fun formatBytes(bytes: Long): String = when {
        bytes >= 1_048_576 -> "${bytes / 1_048_576} Mo"
        bytes >= 1_024 -> "${bytes / 1_024} Ko"
        else -> "$bytes octets"
    }
}
