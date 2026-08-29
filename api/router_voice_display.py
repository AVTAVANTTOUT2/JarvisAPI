"""Snapshot et WebSocket descendants du JARVIS Voice HUD."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

import auth
from api.ws_session import activate_websocket_profile, resolve_websocket_auth
from jarvis.voice_display import voice_display

router = APIRouter()


@router.get("/api/voice-display/snapshot")
async def voice_display_snapshot():
    if not voice_display.enabled():
        raise HTTPException(404, "Voice HUD désactivé")
    return voice_display.snapshot().model_dump(mode="json")


@router.websocket("/ws/voice-display")
async def voice_display_websocket(ws: WebSocket) -> None:
    if not voice_display.enabled():
        await ws.accept()
        await ws.close(code=4404)
        return
    if not await activate_websocket_profile(ws):
        return
    if not auth.is_configured():
        await ws.close(code=4428)
        return
    session, mobile_device = resolve_websocket_auth(ws)
    if not session and not mobile_device:
        await ws.close(code=4401)
        return

    try:
        since = max(0, int(ws.query_params.get("since", "0")))
    except ValueError:
        await ws.close(code=4400)
        return

    await ws.accept()
    subscription = voice_display.subscribe()
    try:
        snapshot = voice_display.snapshot()
        await ws.send_json({
            "type": "voice.display.snapshot",
            "sequence": snapshot.session.last_sequence,
            "snapshot": snapshot.model_dump(mode="json"),
        })
        for event in voice_display.replay(since):
            await ws.send_json(event.model_dump(mode="json"))

        while True:
            event_task = asyncio.create_task(subscription.get())
            receive_task = asyncio.create_task(ws.receive())
            done, pending = await asyncio.wait(
                {event_task, receive_task}, timeout=15, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                await ws.send_json({
                    "type": "voice.display.heartbeat",
                    "sequence": voice_display.snapshot().session.last_sequence,
                })
                continue
            if receive_task in done:
                packet = receive_task.result()
                if packet.get("type") == "websocket.disconnect":
                    break
                raw = packet.get("text")
                try:
                    message = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    message = {}
                if message.get("type") != "pong":
                    await ws.close(code=4405)
                    break
            elif event_task in done:
                event = event_task.result()
                await ws.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        pass
    finally:
        voice_display.unsubscribe(subscription)


__all__ = ["router", "voice_display_snapshot", "voice_display_websocket"]
