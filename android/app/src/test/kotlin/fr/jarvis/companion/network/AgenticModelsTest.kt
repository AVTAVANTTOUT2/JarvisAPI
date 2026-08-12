package fr.jarvis.companion.network

import com.google.gson.Gson
import com.google.gson.JsonParser
import fr.jarvis.companion.core.network.parseAgenticRealtimeEvent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AgenticModelsTest {
    private val gson = Gson()

    @Test
    fun adapterAcceptsGenericRuntimeAndRunEnvelopes() {
        val payload = JsonParser.parseString(
            """
            {
              "runtime": {"ready": true, "state": "ready", "active_runs": 2},
              "runs": [{
                "run_id": "run-1",
                "title": "Préparer la livraison",
                "status": "running",
                "progress": 0.5,
                "approvals": [{"approval_id": "approval-1", "status": "pending"}]
              }]
            }
            """.trimIndent(),
        ).asJsonObject

        val runtime = AgenticJsonAdapter.runtime(payload)
        val runs = AgenticJsonAdapter.runs(payload)

        assertTrue(runtime.isAvailable)
        assertEquals("ready", runtime.displayStatus)
        assertEquals(2, runtime.active_runs)
        assertEquals("run-1", runs.single().stableId)
        assertEquals(50, runs.single().progressPercent)
        assertEquals("approval-1", runs.single().approvals.single().approval_id)
    }

    @Test
    fun realtimeParserRecognizesAgenticEventsAndIgnoresChatEvents() {
        val parsed = JsonParser.parseString(
            """{"type":"agent.approval.requested","data":{"run_id":"run-2","approval_id":"a-2","phase":"reviewing","prompt":"never expose","arguments":{"path":"/private/key"}}}""",
        ).asJsonObject
        val event = parseAgenticRealtimeEvent(parsed, "agent.approval.requested")

        assertEquals("run-2", event?.run_id)
        assertEquals("a-2", event?.approval_id)
        assertEquals("reviewing", event?.phase)
        assertFalse(event?.isTerminal ?: true)
        assertFalse(gson.toJson(event).contains("never expose"))
        assertFalse(gson.toJson(event).contains("/private/key"))
        assertEquals(null, parseAgenticRealtimeEvent(parsed, "response"))
    }

    @Test
    fun realtimeSignalsAreBoundedAndRefreshEverySelectedRunProjection() {
        val oversizedRun = "r".repeat(129)
        val rejected = JsonParser.parseString(
            """{"type":"agent.run.started","payload":{"run_id":"$oversizedRun"}}""",
        ).asJsonObject
        assertEquals(null, parseAgenticRealtimeEvent(rejected, "agent.run.started"))

        val parsed = JsonParser.parseString(
            """{"type":"agent.artifact.created","payload":{"run_id":"run-9","approval_id":"${"a".repeat(129)}"}}""",
        ).asJsonObject
        val event = parseAgenticRealtimeEvent(parsed, "agent.artifact.created")!!
        assertEquals(null, event.approval_id)

        val selected = event.refreshPlan("run-9")
        assertTrue(selected.refreshRuns)
        assertTrue(selected.refreshDetails)
        assertTrue(selected.refreshApprovals)
        assertTrue(selected.refreshArtifacts)

        val other = event.refreshPlan("run-other")
        assertTrue(other.refreshRuns)
        assertFalse(other.refreshDetails)
        assertFalse(other.refreshApprovals)
        assertFalse(other.refreshArtifacts)
    }

    @Test
    fun actionBodiesKeepStableDecisionAndRequestFieldNames() {
        val decision = gson.toJsonTree(AgenticApprovalDecisionRequest("approved")).asJsonObject
        val request = gson.toJsonTree(
            AgenticRunCreateRequest(
                title = "Vérifie le projet",
                run_id = "android-42",
            ),
        ).asJsonObject

        assertEquals("approved", decision.get("decision").asString)
        assertEquals("android-42", request.get("run_id").asString)
        assertEquals("android", request.get("channel").asString)
    }

    @Test
    fun detailAdaptersKeepOnlyUserVisibleEventsAndDecisionState() {
        val payload = JsonParser.parseString(
            """
            {
              "events": [
                {
                  "event_id": "event-public",
                  "run_id": "run-1",
                  "sequence": 4,
                  "type": "agent.tool.started",
                  "visibility": "user",
                  "sensitivity": "normal",
                  "payload": {"prompt": "never decode this", "arguments": {"path": "/private/key"}}
                },
                {
                  "event_id": "event-secret",
                  "run_id": "run-1",
                  "sequence": 5,
                  "type": "agent.tool.completed",
                  "visibility": "user",
                  "sensitivity": "secret"
                }
              ],
              "approvals": [{
                "approval_id": "approval-1",
                "action": "write",
                "tool": "provider-specific-tool",
                "decision": "pending",
                "risks": ["filesystem"]
              }],
              "artifacts": [{
                "artifact_id": "artifact-1",
                "type": "report",
                "reference": "/private/output/report.md",
                "size_bytes": 1024
              }]
            }
            """.trimIndent(),
        ).asJsonObject

        val events = AgenticJsonAdapter.events(payload)
        val approvals = AgenticJsonAdapter.approvals(payload)
        val artifacts = AgenticJsonAdapter.artifacts(payload)

        assertEquals(listOf("event-public"), events.map { it.stableId })
        assertEquals("pending", approvals.single().displayStatus)
        assertTrue(approvals.single().isPending)
        assertEquals(listOf("filesystem"), approvals.single().risks)
        assertEquals("artifact-1", artifacts.single().artifact_id)
    }
}
