"""Endpoint WebSocket de chat temps réel."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

import auth
import config
from api.memory_background import _run_memory_in_background
from api.welcome import _maybe_send_daily_welcome
from api import ws_client_context as ws_ctx
from api.ws_audio_packets import handle_ws_audio_packet
from api.ws_handsfree import _handle_hands_free_blob, handle_voice_cancel_message
from api.ws_messages import _process_message
from api.ws_recordings import WebSocketRecordingController
from api.ws_action_messages import handle_ws_action_decision
from api.ws_conversations import (
    conversation_switched_payload,
    create_websocket_conversation,
    resume_message_checkpoint,
    switch_websocket_conversation,
)
from api.ws_session import (
    _resume_or_create_conversation,
    activate_websocket_profile,
    close_websocket_conversation,
    remember_websocket_conversation,
    resolve_websocket_auth,
    websocket_confirmation_session_id,
)
from database import (
    create_conversation,
    end_conversation,
    get_conversation_detail,
    get_conversation_history,
    get_last_conversation_summary,
    normalize_checkpoint_id,
)
from websocket_registry import add_websocket, remove_websocket

try:
    from audio import stt
except ImportError:
    stt = None

logger = logging.getLogger("jarvis")


async def websocket_endpoint(ws: WebSocket):
    """Chat temps réel : JSON texte, audio binaire, streaming, TTS."""
    if not await activate_websocket_profile(ws):
        return
    if not auth.is_configured():
        await ws.close(code=4428)
        return
    session, mobile_device = resolve_websocket_auth(ws)
    if not session and not mobile_device:
        await ws.close(code=4401)
        return
    confirmation_session_id = websocket_confirmation_session_id(session, mobile_device)
    agentic_context = ws_ctx.websocket_client_context(ws, mobile_device)
    requested_checkpoint_id = ws.query_params.get("checkpoint_id")
    if requested_checkpoint_id:
        try:
            requested_checkpoint_id = normalize_checkpoint_id(requested_checkpoint_id)
        except ValueError:
            await ws.close(code=4400, reason="checkpoint_id invalide")
            return

    await ws.accept()
    if mobile_device:
        logger.info("WS mobile connecté device=%s", mobile_device.get("device_id"))
    else:
        logger.info("WS client connecté")
    await add_websocket(ws)

    conversation_id = None
    checkpoint_id = None
    conversation_mode = False
    is_speaking = False
    conv_audio_buffer: list[bytes] = []
    conv_session: dict | None = None
    recording = WebSocketRecordingController()

    try:
        conversation_id, checkpoint_id, resumed = _resume_or_create_conversation(
            confirmation_session_id,
            requested_checkpoint_id,
        )
        remember_websocket_conversation(
            confirmation_session_id,
            ws,
            conversation_id,
            checkpoint_id,
        )
        try:
            prev = get_last_conversation_summary()
            config.PRIOR_SESSION_SUMMARY = (prev or "").strip()
        except Exception as e:
            logger.exception("get_last_conversation_summary : %s", e)
            config.PRIOR_SESSION_SUMMARY = ""

        await ws.send_json(
            {
                "type": "connected",
                "conversation_id": conversation_id,
                "checkpoint_id": checkpoint_id,
                "user_name": config.USER_NAME,
                "resumed": resumed,
            }
        )
        if not resumed:
            await _maybe_send_daily_welcome(ws)

        while True:
            packet = await ws.receive()

            if packet.get("type") == "websocket.disconnect":
                break

            # ── 1. Audio binaire ──────────────────────────────
            if "bytes" in packet and packet["bytes"] is not None:
                audio_bytes = packet["bytes"]
                is_speaking = await handle_ws_audio_packet(
                    ws,
                    audio_bytes,
                    recording=recording,
                    conv_session=conv_session,
                    is_speaking=is_speaking,
                    conversation_mode=conversation_mode,
                    conv_audio_buffer=conv_audio_buffer,
                    conversation_id=conversation_id,
                    confirmation_session_id=confirmation_session_id,
                    agentic_context=agentic_context,
                    stt=stt,
                    language=config.LANGUAGE,
                    hands_free_fn=_handle_hands_free_blob,
                    process_message_fn=_process_message,
                )
                continue

            # ── 2. Message JSON texte ─────────────────────────
            if "text" in packet and packet["text"] is not None:
                raw = packet["text"]
                try:
                    parsed = ws_ctx.parse_websocket_client_message(raw, agentic_context)
                    msg, msg_type, client_message_id, message_context = parsed
                except (ValueError, TypeError):
                    await ws.send_json({"type": "error", "message": "JSON invalide"})
                    continue
                if await recording.handle_message(
                    ws,
                    msg,
                    msg_type,
                    conversation_id=conversation_id,
                    stt_available=bool(stt and getattr(stt, "available", False)),
                ):
                    continue

                # ── Conversation mains libres (nouveau flux)
                if msg_type == "conversation_start":
                    voice_conversation_id = create_conversation(agent="voice")
                    voice_conversation = get_conversation_detail(voice_conversation_id)
                    conv_session = {
                        "active": True,
                        "conversation_id": voice_conversation_id,
                        "checkpoint_id": voice_conversation.get("checkpoint_id")
                        if voice_conversation
                        else None,
                        "confirmation_session_id": confirmation_session_id,
                        "agentic_context": message_context,
                        "is_speaking": False,
                        "is_processing": False,
                    }
                    logger.info(
                        "[WS] Mains libres démarrées conv_id=%s",
                        conv_session["conversation_id"],
                    )
                    await ws.send_json(
                        {
                            "type": "conversation_started",
                            "conversation_id": conv_session["conversation_id"],
                            "checkpoint_id": conv_session["checkpoint_id"],
                            "silence_duration_ms": config.VOICE_SILENCE_DURATION_MS,
                            "min_speech_ms": config.VOICE_MIN_SPEECH_MS,
                        }
                    )
                    await ws.send_json({"type": "listening"})
                    from jarvis.voice_display import voice_display

                    voice_display.safely(
                        "ensure_turn", conv_session["conversation_id"]
                    )
                    voice_display.safely("publish", "voice.listening.started")
                    continue

                if msg_type == "conversation_stop":
                    if conv_session:
                        try:
                            end_conversation(conv_session["conversation_id"])
                        except Exception as e:
                            logger.error("end_conversation voice : %s", e)
                    conv_session = None
                    await ws.send_json({"type": "conversation_stopped"})
                    continue

                if msg_type == "conversation_mode":
                    conversation_mode = bool(msg.get("enabled", False))
                    conv_audio_buffer.clear()
                    is_speaking = False
                    await ws.send_json(
                        {
                            "type": "conversation_mode",
                            "enabled": conversation_mode,
                        }
                    )
                    if conversation_mode:
                        await ws.send_json({"type": "listening"})
                        logger.info("[WS] Mode conversation (legacy) activé")
                    else:
                        logger.info("[WS] Mode conversation (legacy) désactivé")
                    continue

                if msg_type == "voice_cancel":
                    await handle_voice_cancel_message(ws, conv_session)
                    is_speaking = False
                    continue

                if msg_type == "done_playing":
                    is_speaking = False
                    if conv_session and conv_session.get("active"):
                        conv_session["is_speaking"] = False
                        conv_session.pop("paused_text", None)
                        from jarvis.voice_display import voice_display

                        voice_display.safely("speech_finished")
                        await ws.send_json({"type": "listening"})
                        continue
                    if conversation_mode:
                        conv_audio_buffer.clear()
                        await ws.send_json({"type": "listening"})
                    continue

                if msg_type == "conversation_audio":
                    if is_speaking:
                        continue

                    audio_data = (
                        b"".join(conv_audio_buffer) if conv_audio_buffer else b""
                    )
                    conv_audio_buffer.clear()

                    if not audio_data:
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    if stt is None or not getattr(stt, "available", False):
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": "STT local indisponible (moteur ou modèle absent).",
                            }
                        )
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    await ws.send_json({"type": "processing"})

                    try:
                        text = await stt.transcribe(
                            audio_data, language=config.LANGUAGE
                        )
                    except Exception as e:
                        logger.exception("Erreur STT conversation : %s", e)
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": f"Transcription : {type(e).__name__}",
                            }
                        )
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    if not text or len(text) < 2:
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    await ws.send_json({"type": "transcript", "content": text})

                    try:
                        await _process_message(
                            ws,
                            text,
                            conversation_id,
                            voice_mode=True,
                            stream=True,
                            send_tts=True,
                            confirmation_session_id=confirmation_session_id,
                            agentic_context=agentic_context.agentic_kwargs(),
                        )
                        is_speaking = True
                    except Exception as e:
                        logger.exception("Erreur conversation audio : %s", e)
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": f"Erreur : {type(e).__name__}",
                            }
                        )
                        is_speaking = False
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                    continue

                if await handle_ws_action_decision(
                    ws,
                    msg,
                    conversation_id=conversation_id,
                    confirmation_session_id=confirmation_session_id,
                ):
                    continue

                if msg_type == "new_conversation":
                    try:
                        old_id = conversation_id
                        state = create_websocket_conversation(
                            confirmation_session_id,
                            ws,
                            msg.get("checkpoint_id"),
                        )
                        conversation_id = int(state["conversation_id"])
                        checkpoint_id = str(state["checkpoint_id"])
                        await ws.send_json(conversation_switched_payload(state))
                        logger.info(
                            "[ws] new_conversation #%d (remplace #%s)",
                            conversation_id,
                            old_id,
                        )
                    except Exception as e:
                        logger.exception("[ws] new_conversation : %s", e)
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": f"Impossible de créer la conversation : {e}",
                            }
                        )
                    continue

                if msg_type == "switch_conversation":
                    try:
                        state = switch_websocket_conversation(
                            confirmation_session_id,
                            ws,
                            target_id=msg.get("conversation_id"),
                            target_checkpoint_id=msg.get("checkpoint_id"),
                        )
                        conversation_id = int(state["conversation_id"])
                        checkpoint_id = str(state["checkpoint_id"])
                        await ws.send_json(conversation_switched_payload(state))
                        logger.info("[ws] switch_conversation → #%d", conversation_id)
                    except (LookupError, ValueError) as e:
                        await ws.send_json({"type": "error", "message": str(e)})
                    except Exception as e:
                        logger.exception("[ws] switch_conversation : %s", e)
                        await ws.send_json(
                            {"type": "error", "message": f"Switch échoué : {e}"}
                        )
                    continue

                if msg_type == "loop":
                    task = (msg.get("task") or msg.get("content") or "").strip()
                    if not task:
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": 'Usage : { "type": "loop", "task": "…" }',
                            }
                        )
                        continue
                    try:
                        await _process_message(
                            ws,
                            f"/loop {task}",
                            conversation_id,
                            voice_mode=bool(msg.get("voice_mode")),
                            stream=False,
                            send_tts=bool(msg.get("voice_mode")),
                            confirmation_session_id=confirmation_session_id,
                            client_message_id=client_message_id,
                            agentic_context=message_context.agentic_kwargs(),
                        )
                    except Exception:
                        logger.exception("[ws] loop mode")
                        await ws.send_json(
                            {"type": "error", "message": "Erreur mode autonome"}
                        )
                    continue

                # Message texte classique
                content = (msg.get("content") or "").strip()
                stream = bool(msg.get("stream", True))
                tts_flag = bool(msg.get("tts", False))

                if msg_type != "text" or not content:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Message vide ou type non supporté",
                        }
                    )
                    continue

                try:
                    state = resume_message_checkpoint(
                        confirmation_session_id,
                        ws,
                        conversation_id,
                        msg.get("checkpoint_id"),
                    )
                except (LookupError, ValueError):
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Checkpoint de conversation expiré ou invalide",
                        }
                    )
                    continue
                if state:
                    conversation_id = int(state["conversation_id"])
                    checkpoint_id = str(state["checkpoint_id"])
                    await ws.send_json(conversation_switched_payload(state))

                try:
                    await _process_message(
                        ws,
                        content,
                        conversation_id,
                        voice_mode=False,
                        stream=stream,
                        send_tts=tts_flag,
                        confirmation_session_id=confirmation_session_id,
                        client_message_id=client_message_id,
                        agentic_context=message_context.agentic_kwargs(),
                    )
                    if tts_flag:
                        is_speaking = True
                except Exception:
                    logger.exception("Erreur lors du traitement message texte")
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Erreur agent",
                        }
                    )

    except WebSocketDisconnect:
        logger.info("WS client déconnecté")
    except Exception as e:
        logger.exception("Erreur WS : %s", e)
    finally:
        await remove_websocket(ws)
        # Fenêtre de grâce : une reconnexion rapide reprendra cette conversation.
        if conversation_id and checkpoint_id:
            close_websocket_conversation(
                confirmation_session_id,
                ws,
                conversation_id,
                checkpoint_id,
            )
        if conv_session:
            try:
                end_conversation(conv_session["conversation_id"])
            except Exception as e:
                logger.error("Erreur end_conversation voice : %s", e)
            conv_session = None
        recording.close()
        if conversation_id:
            try:
                history = get_conversation_history(conversation_id, limit=5)
                if len(history) > 2:
                    asyncio.create_task(_run_memory_in_background(conversation_id))
            except Exception as e:
                logger.error(f"Erreur memory background trigger : {e}")
