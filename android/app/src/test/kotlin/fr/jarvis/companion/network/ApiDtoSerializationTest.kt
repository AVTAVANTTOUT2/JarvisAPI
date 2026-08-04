package fr.jarvis.companion.network

import com.google.gson.Gson
import fr.jarvis.companion.voice.VoiceTurnResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiDtoSerializationTest {
    private val gson = Gson()

    @Test
    fun requestDtosPreserveSnakeCaseWireNames() {
        val json = gson.toJsonTree(
            MobileChatRequest(
                content = "Bonjour",
                conversation_id = 42,
                client_message_id = "android-123",
            ),
        ).asJsonObject

        assertEquals("Bonjour", json["content"].asString)
        assertEquals(42L, json["conversation_id"].asLong)
        assertEquals("android-123", json["client_message_id"].asString)
        assertFalse(json.has("conversationId"))
        assertFalse(json.has("clientMessageId"))
    }

    @Test
    fun locationBatchResponseDeserializesNestedDtos() {
        val response = gson.fromJson(
            """{
                "accepted":["point-1"],
                "duplicates":["point-2"],
                "rejected":[{"client_point_id":"point-3","reason":"invalid_accuracy"}]
            }""".trimIndent(),
            LocationBatchResponse::class.java,
        )

        assertEquals(listOf("point-1"), response.accepted)
        assertEquals(listOf("point-2"), response.duplicates)
        assertEquals("point-3", response.rejected.single().client_point_id)
        assertEquals("invalid_accuracy", response.rejected.single().reason)
    }

    @Test
    fun diagnosticsAndVoiceResponsesKeepApiFieldMappings() {
        val diagnostics = gson.fromJson(
            """{
                "device_id":"pixel-9",
                "points_received_24h":17,
                "last_point_received_at":null
            }""".trimIndent(),
            LocationDiagnosticsResponse::class.java,
        )
        val voice = gson.fromJson(
            """{
                "conversation_id":7,
                "transcript":"bonjour",
                "response_text":"Bonjour Monsieur.",
                "audio_base64":null,
                "audio_mime_type":null,
                "stt_engine":"faster-whisper",
                "stt_model":"small",
                "tts_engine":"local",
                "source":"android_voice",
                "device_id":"pixel-9",
                "tts_error":null
            }""".trimIndent(),
            VoiceTurnResponse::class.java,
        )

        assertEquals("pixel-9", diagnostics.device_id)
        assertEquals(17, diagnostics.points_received_24h)
        assertNull(diagnostics.last_point_received_at)
        assertEquals(7L, voice.conversationId)
        assertEquals("Bonjour Monsieur.", voice.responseText)
        assertEquals("small", voice.sttModel)
        assertTrue(voice.audioBase64 == null)
    }
}
