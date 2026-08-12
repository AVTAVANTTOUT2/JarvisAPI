package fr.jarvis.companion.network

import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonObject

/** Provider-neutral wire contracts for long-running JARVIS work. */
data class AgenticRuntimeStatusDto(
    val available: Boolean? = null,
    val healthy: Boolean? = null,
    val ready: Boolean? = null,
    val status: String = "unknown",
    val state: String? = null,
    val mode: String? = null,
    val label: String? = null,
    val active_runs: Int = 0,
    val queued_runs: Int = 0,
    val checked_at: String? = null,
    val error_code: String? = null,
) {
    val isAvailable: Boolean
        get() = available ?: healthy ?: ready ?: (status in setOf("available", "healthy", "ready", "running"))

    val displayStatus: String
        get() = state?.takeIf { status == "unknown" } ?: status
}

data class AgenticStepDto(
    val id: String? = null,
    val step_id: String? = null,
    val title: String? = null,
    val name: String? = null,
    val status: String = "pending",
    val kind: String? = null,
    val summary: String? = null,
    val progress: Double? = null,
)

data class AgenticApprovalDto(
    val id: String? = null,
    val approval_id: String? = null,
    val title: String? = null,
    val action: String? = null,
    val summary: String? = null,
    val status: String = "pending",
    val decision: String? = null,
    val tool: String? = null,
    val risks: List<String> = emptyList(),
    val risk_level: String? = null,
    val requested_at: String? = null,
    val resolved_at: String? = null,
    val expires_at: String? = null,
) {
    val stableId: String
        get() = id ?: approval_id.orEmpty()

    val displayStatus: String
        get() = decision ?: status

    val isPending: Boolean
        get() = displayStatus == "pending"
}

data class AgenticArtifactDto(
    val id: String? = null,
    val artifact_id: String? = null,
    val name: String? = null,
    val filename: String? = null,
    val kind: String? = null,
    val type: String? = null,
    val mime_type: String? = null,
    val size_bytes: Long? = null,
    val url: String? = null,
    val reference: String? = null,
    val metadata: JsonObject? = null,
    val created_at: String? = null,
)

data class AgenticErrorDto(
    val code: String? = null,
    val category: String? = null,
    val message: String = "",
    val retryable: Boolean? = null,
)

/** Event payloads intentionally stay opaque: native UI never renders raw prompts or tool arguments. */
data class AgenticEventDto(
    val id: String? = null,
    val event_id: String? = null,
    val run_id: String? = null,
    val sequence: Long = 0,
    val type: String = "agent.run.updated",
    val timestamp: String? = null,
    val level: String = "info",
    val visibility: String = "user",
    val sensitivity: String = "normal",
) {
    val stableId: String
        get() = id ?: event_id ?: "$run_id:$sequence:$type"
}

data class AgenticRunDto(
    val id: String? = null,
    val run_id: String? = null,
    val title: String = "Tâche agentique",
    val status: String = "created",
    val phase: String? = null,
    val progress: Double? = null,
    val summary: String? = null,
    val channel: String? = null,
    val category: String? = null,
    val runtime_id: String? = null,
    val task_id: String? = null,
    val conversation_id: String? = null,
    val requires_attention: Boolean = false,
    val created_at: String? = null,
    val updated_at: String? = null,
    val started_at: String? = null,
    val completed_at: String? = null,
    val finished_at: String? = null,
    val plan: JsonElement? = null,
    val steps: List<AgenticStepDto> = emptyList(),
    val approvals: List<AgenticApprovalDto> = emptyList(),
    val artifacts: List<AgenticArtifactDto> = emptyList(),
    val result: JsonElement? = null,
    val verification: JsonElement? = null,
    val error: AgenticErrorDto? = null,
    val error_message: String? = null,
) {
    val stableId: String
        get() = id ?: run_id.orEmpty()

    val progressPercent: Int?
        get() = progress?.let { raw ->
            val percent = if (raw > 0.0 && raw <= 1.0) raw * 100 else raw
            percent.toInt().coerceIn(0, 100)
        }
}

data class AgenticRunCreateRequest(
    val title: String,
    val category: String = "direct_action",
    val origin: String = "user",
    val channel: String = "android",
    val run_id: String? = null,
)

data class AgenticApprovalDecisionRequest(val decision: String)

data class AgenticRealtimeEventDto(
    val type: String,
    val run_id: String,
    val status: String? = null,
    val phase: String? = null,
    val progress: Double? = null,
    val approval_id: String? = null,
    val requires_attention: Boolean? = null,
) {
    val isTerminal: Boolean
        get() = type in setOf("agent.run.completed", "agent.run.failed", "agent.run.cancelled")

    fun refreshPlan(selectedRunId: String?): AgenticRealtimeRefreshPlan {
        val refreshSelected = selectedRunId == run_id
        return AgenticRealtimeRefreshPlan(
            refreshRuns = true,
            refreshDetails = refreshSelected,
            refreshApprovals = refreshSelected,
            refreshArtifacts = refreshSelected,
        )
    }
}

data class AgenticRealtimeRefreshPlan(
    val refreshRuns: Boolean,
    val refreshDetails: Boolean,
    val refreshApprovals: Boolean,
    val refreshArtifacts: Boolean,
)

data class AgenticCallResult<T>(
    val ok: Boolean,
    val status: Int,
    val value: T? = null,
    val unauthorized: Boolean = false,
    val error: String = "",
)

/** Keeps envelope differences at the network boundary while the server contract settles. */
object AgenticJsonAdapter {
    private val gson = Gson()

    fun runtime(payload: JsonObject): AgenticRuntimeStatusDto {
        val runtimeList = payload.get("runtimes")?.takeIf { it.isJsonArray }?.asJsonArray
            ?.mapNotNull { item -> item.takeIf { it.isJsonObject }?.asJsonObject }
        val selected = runtimeList?.firstOrNull { item ->
            item.get("status")?.asString !in setOf("unavailable", "offline")
        } ?: runtimeList?.firstOrNull()
        val source = objectAt(payload, "runtime", "agentic_runtime") ?: selected ?: payload
        return gson.fromJson(source, AgenticRuntimeStatusDto::class.java)
    }

    fun runs(payload: JsonObject): List<AgenticRunDto> {
        val array = sequenceOf("runs", "items", "results")
            .mapNotNull { key -> payload.get(key)?.takeIf { it.isJsonArray }?.asJsonArray }
            .firstOrNull()
            ?: return emptyList()
        return array.mapNotNull { element ->
            element.takeIf { it.isJsonObject }?.let { gson.fromJson(it, AgenticRunDto::class.java) }
        }.filter { it.stableId.isNotBlank() }
    }

    fun run(payload: JsonObject): AgenticRunDto {
        val source = objectAt(payload, "run") ?: payload
        return gson.fromJson(source, AgenticRunDto::class.java)
    }

    fun events(payload: JsonObject): List<AgenticEventDto> =
        arrayAt(payload, "events").mapNotNull { element ->
            element.takeIf { it.isJsonObject }?.let { gson.fromJson(it, AgenticEventDto::class.java) }
        }.filter { it.visibility == "user" && it.sensitivity !in setOf("secret", "private") }

    fun approvals(payload: JsonObject): List<AgenticApprovalDto> =
        arrayAt(payload, "approvals").mapNotNull { element ->
            element.takeIf { it.isJsonObject }?.let { gson.fromJson(it, AgenticApprovalDto::class.java) }
        }.filter { it.stableId.isNotBlank() }

    fun artifacts(payload: JsonObject): List<AgenticArtifactDto> =
        arrayAt(payload, "artifacts").mapNotNull { element ->
            element.takeIf { it.isJsonObject }?.let { gson.fromJson(it, AgenticArtifactDto::class.java) }
        }

    private fun arrayAt(payload: JsonObject, key: String): Iterable<JsonElement> =
        payload.get(key)?.takeIf { it.isJsonArray }?.asJsonArray ?: emptyList<JsonElement>()

    private fun objectAt(payload: JsonObject, vararg keys: String): JsonObject? =
        keys.firstNotNullOfOrNull { key -> payload.get(key)?.takeIf { it.isJsonObject }?.asJsonObject }
}
