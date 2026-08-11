"""Endpoint WebSocket de chat temps réel."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

import auth
import config
from api.chat_actions import _run_loop_mode_ws
from api.memory_background import _run_memory_in_background
from api.welcome import _maybe_send_daily_welcome
from api.ws_handsfree import _handle_hands_free_blob, handle_voice_cancel_message
from api.ws_messages import _process_message
from api.ws_action_messages import handle_ws_action_decision
from api.ws_conversations import (
    conversation_switched_payload,
    create_websocket_conversation,
    resume_message_checkpoint,
    switch_websocket_conversation,
)
from api.ws_session import (
    _resume_or_create_conversation,
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
    save_message,
)
from websocket_registry import add_websocket, remove_websocket

try:
    from audio import stt
except ImportError:
    stt = None

logger = logging.getLogger("jarvis")

async def websocket_endpoint(ws: WebSocket):
    """Chat temps réel : JSON texte, audio binaire, streaming, TTS."""
    from database import activate_profile, normalize_profile_id, user_profile_exists

    try:
        profile_id = normalize_profile_id(
            ws.headers.get("x-jarvis-profile") or ws.cookies.get("jarvis_profile")
        )
    except ValueError:
        await ws.close(code=4400, reason="profil invalide")
        return
    if not user_profile_exists(profile_id):
        await ws.close(code=4404, reason="profil introuvable")
        return
    # Chaque WebSocket possède sa propre tâche asyncio : le contexte disparaît
    # avec elle et reste hérité par ses tâches de mémoire en arrière-plan.
    activate_profile(profile_id)
    if not auth.is_configured():
        await ws.close(code=4428)
        return
    session, mobile_device = resolve_websocket_auth(ws)
    if not session and not mobile_device:
        await ws.close(code=4401)
        return
    confirmation_session_id = websocket_confirmation_session_id(session, mobile_device)
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
    active_recording = None

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

        await ws.send_json({
            "type": "connected",
            "conversation_id": conversation_id,
            "checkpoint_id": checkpoint_id,
            "user_name": config.USER_NAME,
            "resumed": resumed,
        })
        if not resumed:
            await _maybe_send_daily_welcome(ws)

        while True:
            packet = await ws.receive()

            if packet.get("type") == "websocket.disconnect":
                break

            # ── 1. Audio binaire ──────────────────────────────
            if "bytes" in packet and packet["bytes"] is not None:
                audio_bytes = packet["bytes"]

                if active_recording is not None and getattr(active_recording, "is_active", False):
                    active_recording.add_chunk(audio_bytes)
                    continue

                # Mains libres : barge-in autorisé si is_speaking ; ignore si processing seul
                if conv_session and conv_session.get("active"):
                    if conv_session.get("is_processing") and not conv_session.get("is_speaking"):
                        continue
                    await _handle_hands_free_blob(ws, audio_bytes, conv_session)
                    continue

                # PTT : pendant TTS, attendre le JSON voice_cancel (pas de blob)
                if is_speaking:
                    continue

                if conversation_mode:
                    conv_audio_buffer.append(audio_bytes)
                    continue

                # Poussoir (un blob)
                logger.info("Audio reçu poussoir : %d bytes", len(audio_bytes))

                if stt is None or not getattr(stt, "available", False):
                    await ws.send_json({
                        "type": "error",
                        "message": "STT local indisponible (moteur ou modèle absent).",
                    })
                    continue

                await ws.send_json({"type": "status", "content": "Transcription en cours…"})

                try:
                    text = await stt.transcribe(audio_bytes, language=config.LANGUAGE)
                except Exception as e:
                    logger.exception("Erreur STT : %s", e)
                    await ws.send_json({
                        "type": "error",
                        "message": f"Erreur transcription : {type(e).__name__}",
                    })
                    continue

                if not text or len(text) < 2:
                    await ws.send_json({
                        "type": "error",
                        "message": "Je n'ai pas compris, réessaie.",
                    })
                    continue

                await ws.send_json({"type": "transcript", "content": text})

                try:
                    await _process_message(
                        ws, text, conversation_id, voice_mode=True, stream=True,
                        send_tts=True, confirmation_session_id=confirmation_session_id,
                    )
                    is_speaking = True  # jusqu'à done_playing (réponse vocale jouée)
                except Exception as e:
                    logger.exception("Erreur traitement message audio")
                    await ws.send_json({
                        "type": "error",
                        "message": f"Erreur agent : {type(e).__name__}: {e}",
                    })
                continue

            # ── 2. Message JSON texte ─────────────────────────
            if "text" in packet and packet["text"] is not None:
                raw = packet["text"]
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "JSON invalide"})
                    continue

                msg_type = msg.get("type", "text")

                if msg_type == "recording_start":
                    if stt is None or not getattr(stt, "available", False):
                        await ws.send_json({
                            "type": "error",
                            "message": "STT local indisponible (moteur ou modèle absent).",
                        })
                        continue
                    from audio.continuous_recorder import ContinuousRecording

                    label = str(msg.get("label") or "Enregistrement").strip()[:200]
                    active_recording = ContinuousRecording(conversation_id)
                    active_recording.label = label
                    active_recording.is_active = True
                    logger.info("[WS] Écoute continue — label=%s", label)
                    await ws.send_json({"type": "recording_started", "label": label})
                    continue

                if msg_type == "recording_stop":
                    if active_recording is None:
                        await ws.send_json({"type": "error", "message": "Aucun enregistrement en cours."})
                        continue
                    rec = active_recording
                    active_recording = None

                    async def _recording_progress(event: str, payload: dict) -> None:
                        await ws.send_json({"type": event, **payload})

                    await ws.send_json({"type": "recording_processing", "message": "Transcription en cours…"})
                    try:
                        result = await rec.stop_and_process(progress=_recording_progress)
                    except Exception as e:
                        logger.exception("[WS] recording_stop : %s", e)
                        await ws.send_json({
                            "type": "recording_done",
                            "result": {"ok": False, "error": str(e), "label": getattr(rec, "label", "")},
                        })
                        continue
                    await ws.send_json({"type": "recording_done", "result": result})
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
                        "is_speaking": False,
                        "is_processing": False,
                    }
                    logger.info("[WS] Mains libres démarrées conv_id=%s", conv_session["conversation_id"])
                    await ws.send_json({
                        "type": "conversation_started",
                        "conversation_id": conv_session["conversation_id"],
                        "checkpoint_id": conv_session["checkpoint_id"],
                        "silence_duration_ms": config.VOICE_SILENCE_DURATION_MS,
                        "min_speech_ms": config.VOICE_MIN_SPEECH_MS,
                    })
                    await ws.send_json({"type": "listening"})
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
                    await ws.send_json({
                        "type": "conversation_mode",
                        "enabled": conversation_mode,
                    })
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
                        await ws.send_json({"type": "listening"})
                        continue
                    if conversation_mode:
                        conv_audio_buffer.clear()
                        await ws.send_json({"type": "listening"})
                    continue

                if msg_type == "conversation_audio":
                    if is_speaking:
                        continue

                    audio_data = b"".join(conv_audio_buffer) if conv_audio_buffer else b""
                    conv_audio_buffer.clear()

                    if not audio_data:
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    if stt is None or not getattr(stt, "available", False):
                        await ws.send_json({
                            "type": "error",
                            "message": "STT local indisponible (moteur ou modèle absent).",
                        })
                        if conversation_mode:
                            await ws.send_json({"type": "listening"})
                        continue

                    await ws.send_json({"type": "processing"})

                    try:
                        text = await stt.transcribe(audio_data, language=config.LANGUAGE)
                    except Exception as e:
                        logger.exception("Erreur STT conversation : %s", e)
                        await ws.send_json({
                            "type": "error",
                            "message": f"Transcription : {type(e).__name__}",
                        })
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
                            ws, text, conversation_id, voice_mode=True, stream=True,
                            send_tts=True, confirmation_session_id=confirmation_session_id,
                        )
                        is_speaking = True
                    except Exception as e:
                        logger.exception("Erreur conversation audio : %s", e)
                        await ws.send_json({
                            "type": "error",
                            "message": f"Erreur : {type(e).__name__}",
                        })
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
                        logger.info("[ws] new_conversation #%d (remplace #%s)", conversation_id, old_id)
                    except Exception as e:
                        logger.exception("[ws] new_conversation : %s", e)
                        await ws.send_json({"type": "error", "message": f"Impossible de créer la conversation : {e}"})
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
                        await ws.send_json({"type": "error", "message": f"Switch échoué : {e}"})
                    continue

                if msg_type == "loop":
                    task = (msg.get("task") or msg.get("content") or "").strip()
                    if not task:
                        await ws.send_json({
                            "type": "error",
                            "message": "Usage : { \"type\": \"loop\", \"task\": \"…\" }",
                        })
                        continue
                    try:
                        save_message(conversation_id, "user", f"/loop {task}")
                    except Exception as e:
                        logger.debug("[ws] loop save user : %s", e)
                    try:
                        await _run_loop_mode_ws(
                            ws, task, conversation_id,
                            voice_mode=bool(msg.get("voice_mode")),
                            confirmation_session_id=confirmation_session_id,
                        )
                    except Exception:
                        logger.exception("[ws] loop mode")
                        await ws.send_json({"type": "error", "message": "Erreur mode autonome"})
                    continue

                # Message texte classique
                content = (msg.get("content") or "").strip()
                stream = bool(msg.get("stream", True))
                tts_flag = bool(msg.get("tts", False))

                if msg_type != "text" or not content:
                    await ws.send_json({
                        "type": "error",
                        "message": "Message vide ou type non supporté",
                    })
                    continue

                try:
                    state = resume_message_checkpoint(
                        confirmation_session_id,
                        ws,
                        conversation_id,
                        msg.get("checkpoint_id"),
                    )
                except (LookupError, ValueError):
                    await ws.send_json({
                        "type": "error",
                        "message": "Checkpoint de conversation expiré ou invalide",
                    })
                    continue
                if state:
                    conversation_id = int(state["conversation_id"])
                    checkpoint_id = str(state["checkpoint_id"])
                    await ws.send_json(conversation_switched_payload(state))

                try:
                    await _process_message(
                        ws, content, conversation_id, voice_mode=False, stream=stream,
                        send_tts=tts_flag, confirmation_session_id=confirmation_session_id,
                    )
                    if tts_flag:
                        is_speaking = True
                except Exception:
                    logger.exception("Erreur lors du traitement message texte")
                    await ws.send_json({
                        "type": "error",
                        "message": "Erreur agent",
                    })

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
        if conversation_id:
            try:
                history = get_conversation_history(conversation_id, limit=5)
                if len(history) > 2:
                    asyncio.create_task(_run_memory_in_background(conversation_id))
            except Exception as e:
                logger.error(f"Erreur memory background trigger : {e}")
