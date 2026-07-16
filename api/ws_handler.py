"""Endpoint WebSocket de chat temps réel."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

import auth
import config
from actions import execute_action
from agents.display_text import finalize_assistant_display_text
from agents.orchestrator import orchestrator
from api.chat_actions import ACTIONS_WITH_FOLLOWUP, _format_action_result_for_followup, _run_loop_mode_ws
from api.llm_logging import _schedule_llm_log
from api.memory_background import _run_memory_in_background
from api.welcome import _maybe_send_daily_welcome
from api.ws_handsfree import _handle_hands_free_blob
from api.ws_messages import _process_message
from api.ws_session import _resume_or_create_conversation, _ws_last_session
from database import create_conversation, end_conversation, get_conversation_detail, get_conversation_history, get_last_conversation_summary, save_message
from websocket_registry import add_websocket, remove_websocket

try:
    from audio import stt
except ImportError:
    stt = None

logger = logging.getLogger("jarvis")


async def websocket_endpoint(ws: WebSocket):
    """Chat temps réel + mode conversation vocale continue.

    Accepte côté client :
    - JSON texte : `{type: "text"|"action_confirm"|"conversation_mode"|"done_playing", ...}`
    - Bytes bruts : audio enregistré au micro (webm/opus)

    Renvoie côté serveur :
    - Events streaming JSON (classification, chunk, done, saved_file, error)
    - `transcript` après STT
    - `speaking` avant envoi audio TTS (le client arrête le micro)
    - `listening` quand JARVIS a fini de parler (le client reprend le micro)
    - Bytes MP3 pour la réponse TTS
    """
    if not auth.is_configured():
        await ws.close(code=4428)
        return
    session = auth.verify_session(ws.cookies.get(config.SESSION_COOKIE_NAME))
    mobile_device = None
    if not session:
        # Companion Android : Authorization Bearer au handshake (jamais en query).
        authorization = ws.headers.get("authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            mobile_device = auth.verify_mobile_token(token.strip())
    if not session and not mobile_device:
        await ws.close(code=4401)
        return

    await ws.accept()
    if mobile_device:
        logger.info("WS mobile connecté device=%s", mobile_device.get("device_id"))
    else:
        logger.info("WS client connecté")
    await add_websocket(ws)

    conversation_id = None
    conversation_mode = False  # ancien flux (conversation_audio + fragments)
    is_speaking = False       # chat / poussoir
    conv_audio_buffer: list[bytes] = []
    conv_session: dict | None = None   # mains libres (conversation_start)
    active_recording = None  # audio.continuous_recorder.ContinuousRecording | None

    try:
        conversation_id, resumed = _resume_or_create_conversation()
        _ws_last_session.update({"conversation_id": conversation_id, "closed_at": 0.0, "ws": ws})
        try:
            prev = get_last_conversation_summary()
            config.PRIOR_SESSION_SUMMARY = (prev or "").strip()
        except Exception as e:
            logger.exception("get_last_conversation_summary : %s", e)
            config.PRIOR_SESSION_SUMMARY = ""

        await ws.send_json({
            "type": "connected",
            "conversation_id": conversation_id,
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

                # Mains libres : un blob WebM complet par utterance (VAD navigateur)
                if conv_session and conv_session.get("active"):
                    if conv_session.get("is_speaking") or conv_session.get("is_processing"):
                        continue
                    await _handle_hands_free_blob(ws, audio_bytes, conv_session)
                    continue

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
                        ws, text, conversation_id, voice_mode=True, stream=True, send_tts=True,
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
                    conv_session = {
                        "active": True,
                        "conversation_id": create_conversation(agent="voice"),
                        "is_speaking": False,
                        "is_processing": False,
                    }
                    logger.info("[WS] Mains libres démarrées conv_id=%s", conv_session["conversation_id"])
                    await ws.send_json({
                        "type": "conversation_started",
                        "conversation_id": conv_session["conversation_id"],
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
                            ws, text, conversation_id, voice_mode=True, stream=True, send_tts=True,
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

                if msg_type == "action_confirm":
                    act = msg.get("action")
                    if not isinstance(act, dict) or not act.get("type"):
                        await ws.send_json({"type": "error", "message": "action invalide"})
                        continue
                    act = {**act, "confirmed": True}
                    _schedule_llm_log(
                        agent="orchestrator",
                        action_type=str(act.get("type") or "unknown"),
                        payload={"conversation_id": conversation_id, "action": act, "confirmed": True},
                        status="pending",
                    )
                    try:
                        res = await execute_action(act)
                    except Exception as e:
                        logger.exception("action_confirm : %s", e)
                        await ws.send_json({
                            "type": "action_result",
                            "action": act.get("type"),
                            "result": {"ok": False, "message": str(e)},
                        })
                        continue
                    await ws.send_json({
                        "type": "action_result",
                        "action": act.get("type"),
                        "action_payload": act,
                        "result": res,
                    })
                    if (
                        res.get("ok")
                        and act.get("type") in ACTIONS_WITH_FOLLOWUP
                        and not res.get("needs_confirmation")
                    ):
                        try:
                            payload = _format_action_result_for_followup(act, res)
                            await ws.send_json({"type": "status", "content": "Synthèse du résultat…"})
                            fu = await orchestrator.handle(
                                (
                                    f"Résultat brut de l'action :\n\n{payload}\n\n"
                                    "L'utilisateur a confirmé l'exécution. Résume le résultat de façon claire. "
                                    "Pas de bloc action."
                                ),
                                conversation_id=conversation_id,
                                voice_mode=False,
                            )
                            txt = finalize_assistant_display_text(fu.get("response", ""))
                            await ws.send_json({"type": "response_followup", "content": txt})
                            try:
                                save_message(
                                    conversation_id, "assistant", txt,
                                    agent=fu.get("agent"),
                                    model=fu.get("model"),
                                    tokens_in=int(fu.get("tokens_in") or 0),
                                    tokens_out=int(fu.get("tokens_out") or 0),
                                    cost=float(fu.get("cost") or 0.0),
                                )
                            except Exception as e:
                                logger.error("save followup action_confirm : %s", e)
                        except Exception as e:
                            logger.exception("[action_confirm] followup : %s", e)
                    continue

                if msg_type == "new_conversation":
                    try:
                        old_id = conversation_id
                        conversation_id = create_conversation(agent="orchestrator")
                        await ws.send_json({
                            "type": "conversation_switched",
                            "conversation_id": conversation_id,
                            "title": None,
                        })
                        logger.info("[ws] new_conversation #%d (remplace #%s)", conversation_id, old_id)
                    except Exception as e:
                        logger.exception("[ws] new_conversation : %s", e)
                        await ws.send_json({"type": "error", "message": f"Impossible de créer la conversation : {e}"})
                    continue

                if msg_type == "switch_conversation":
                    target_id = msg.get("conversation_id")
                    if not isinstance(target_id, int):
                        await ws.send_json({"type": "error", "message": "conversation_id manquant"})
                        continue
                    try:
                        conv = get_conversation_detail(target_id)
                        if not conv:
                            await ws.send_json({"type": "error", "message": f"Conversation #{target_id} introuvable"})
                            continue
                        conversation_id = target_id
                        await ws.send_json({
                            "type": "conversation_switched",
                            "conversation_id": conversation_id,
                            "title": conv.get("title"),
                        })
                        logger.info("[ws] switch_conversation → #%d", conversation_id)
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
                            ws, task, conversation_id, voice_mode=bool(msg.get("voice_mode")),
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
                    await _process_message(
                        ws, content, conversation_id, voice_mode=False, stream=stream, send_tts=tts_flag,
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
        if conversation_id:
            import time as _time

            _ws_last_session["conversation_id"] = conversation_id
            _ws_last_session["closed_at"] = _time.time()
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
            try:
                end_conversation(conversation_id)
            except Exception as e:
                logger.error(f"Erreur end_conversation : {e}")
