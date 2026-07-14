"""Traitement contextualisé des questions sur un contact."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Body, HTTPException
from fastapi.responses import JSONResponse

import config
import llm
import pipeline
from agents.display_text import strip_leading_emotion
from api.people_support import (
    _decode_person_path,
    _format_contact_timeline,
    _format_imessage_snippets,
    _format_people_events,
    _load_persona_block,
    _resolve_handle_with_contacts,
)
from database import (
    create_conversation,
    get_person,
    get_relationship_profile,
    get_relationship_timeline,
    save_message,
    update_conversation_activity,
)

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")


async def api_people_ask(name: str, payload: dict[str, Any] = Body(default_factory=dict)):
    """Pose une question contextualisée sur un contact (Sonnet + chat.db + profil)."""
    path_decoded = _decode_person_path(name)
    try:
        question = (payload.get("question") or "").strip()
        logger.info("[contact_chat] path=%r decoded=%r question_len=%d", name, path_decoded, len(question))
        if not question:
            raise HTTPException(400, "`question` requis")

        person = None
        try:
            person = get_person(path_decoded)
            if not person and path_decoded != name.strip():
                person = get_person(name.strip())
            logger.info(
                "[contact_chat] person trouvée=%s id=%s",
                person is not None,
                person.get("id") if person else None,
            )
        except Exception:
            logger.exception("[contact_chat] get_person")
            raise

        if not person:
            return {
                "response": f"Je n'ai pas de fiche pour « {path_decoded} ».",
                "model": None,
                "cost": 0.0,
            }

        pid = person["id"]
        profile = None
        try:
            profile = get_relationship_profile(pid)
            logger.info("[contact_chat] profil relationnel présent=%s", profile is not None)
        except Exception as e:
            logger.warning("[contact_chat] profil relationnel ignoré : %s", e)

        timeline: list = []
        try:
            timeline = get_relationship_timeline(pid, limit=28)
            logger.info("[contact_chat] timeline événements=%d", len(timeline))
        except Exception as e:
            logger.warning("[contact_chat] timeline ignorée : %s", e)

        msgs: list = []
        try:
            from integrations.imessage_reader import imessage_reader

            if imessage_reader and imessage_reader.is_available():
                handle = _resolve_handle_with_contacts(person.get("name") or path_decoded)
                if handle:
                    msgs = imessage_reader.get_recent_conversation(handle, limit=30)
                    logger.info("[contact_chat] iMessage via handle len=%d", len(msgs))
                else:
                    nm = person.get("name") or path_decoded
                    msgs = imessage_reader.get_conversation_with(nm, limit=30)
                    logger.info("[contact_chat] iMessage via nom len=%d", len(msgs))
            else:
                logger.info("[contact_chat] iMessage reader indisponible")
        except Exception as e:
            logger.warning("[contact_chat] erreur iMessage : %s", e)

        rp = profile or {}
        events_block = "(aucun événement)"
        try:
            events_block = (
                _format_people_events(person.get("events"))
                + "\n\n— Timeline relationnelle —\n"
                + _format_contact_timeline(timeline)
            )
        except Exception as e:
            logger.warning("[contact_chat] format événements : %s", e)

        snippets = "(aucun extrait iMessage — handle ou chat.db)"
        try:
            snippets = _format_imessage_snippets(msgs, person.get("name") or path_decoded)
        except Exception as e:
            logger.warning("[contact_chat] format extraits : %s", e)

        tpl = ""
        try:
            tpl = (BASE_DIR / "prompts" / "contact_chat.txt").read_text(encoding="utf-8")
            logger.info("[contact_chat] prompts/contact_chat.txt chargé (%d car.)", len(tpl))
        except OSError as e:
            logger.warning("[contact_chat] contact_chat.txt absent, fallback persona : %s", e)
            tpl = _load_persona_block()

        try:
            system = (
                tpl.replace("{{persona}}", _load_persona_block())
                .replace("{{contact_name}}", person.get("name") or path_decoded)
                .replace("{{relationship}}", str(person.get("relationship") or "—"))
                .replace("{{personality_notes}}", str(person.get("personality_notes") or "—"))
                .replace("{{dynamics}}", str(person.get("dynamics") or "—"))
                .replace("{{patterns}}", str(person.get("patterns") or "—"))
                .replace("{{communication_style}}", str(rp.get("communication_style") or "—"))
                .replace("{{sentiment}}", str(rp.get("sentiment") or "—"))
                .replace("{{trust_level}}", str(rp.get("trust_level") or "—"))
                .replace("{{events}}", events_block)
                .replace("{{recent_messages}}", snippets)
            )
            logger.info("[contact_chat] system prompt construit (%d car.)", len(system))
        except Exception:
            logger.exception("[contact_chat] construction du prompt système")
            raise

        # Construire un message enrichi avec tout le contexte de la personne
        profile_text = ""
        if profile:
            profile_text = (
                f"\n[PROFIL DE {(person.get('name') or path_decoded).upper()}]\n"
                f"Relation : {profile.get('relationship') or person.get('relationship') or '?'}\n"
                f"Sentiment : {profile.get('sentiment') or '?'}\n"
                f"Style communication : {profile.get('communication_style') or '?'}\n"
                f"Confiance : {profile.get('trust_level') or '?'}\n"
            )

        enriched_question = (
            f"[QUESTION SUR {(person.get('name') or path_decoded).upper()}]"
            f"{profile_text}"
            f"\nÉvénements récents :\n{events_block}"
            f"\n\nDerniers échanges iMessage :\n{snippets}"
            f"\n\nQuestion : {question}"
        )

        try:
            # Créer une conversation temporaire pour cette question contact
            conv_id = create_conversation(agent="contact_chat")
            save_message(conv_id, "user", question)
            update_conversation_activity(conv_id)

            # Passer par le pipeline unifié — bénéficie de TOUT le contexte
            result = await pipeline.process_message_internal(enriched_question, conv_id)
            logger.info("[contact_chat] pipeline unifié ok model=%s", result.get("model"))
            return {
                "response": result.get("text", ""),
                "model": result.get("model"),
                "cost": result.get("cost", 0.0),
            }
        except Exception as e:
            logger.exception("[contact_chat] pipeline unifié : %s — fallback LLM direct", e)
            # Fallback : appel LLM direct avec le prompt spécialisé contact_chat
            res = await llm.chat(
                messages=[{"role": "user", "content": question}],
                model=config.DEEPSEEK_MAIN_MODEL,
                system=system,
                max_tokens=1800,
                temperature=0.45,
                use_cache=False,
            )
            text = strip_leading_emotion((res.get("content") or "").strip())
            logger.info("[contact_chat] fallback LLM ok model=%s", res.get("model"))
            return {
                "response": text,
                "model": res.get("model"),
                "cost": res.get("cost", 0.0),
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[contact_chat] ERREUR : %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})



