"""Fonctions de support des routes people."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import unquote

import config
import llm
from agents.display_text import strip_leading_emotion

BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger("jarvis")


def _decode_person_path(name: str) -> str:
    """Segment de chemin `/api/people/{name}` — décodage %XX supplémentaire si besoin."""
    return unquote(name).strip()


def _load_persona_block() -> str:
    try:
        raw = (BASE_DIR / "prompts" / "persona.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    return raw.replace("{{user_name}}", getattr(config, "USER_NAME", "l'utilisateur"))


def _resolve_imessage_handle(person: dict, profile: dict | None) -> str | None:
    if profile and profile.get("handle"):
        h = str(profile["handle"]).strip()
        if h:
            return h
    n = (person.get("name") or "").strip()
    if "@" in n:
        return n
    if re.match(r"^\+?\d[\d\s\-\(\)\.]+$", n):
        return re.sub(r"\s+", "", n)
    return None


def _resolve_handle_with_contacts(name: str) -> str | None:
    """Résout un nom de contact en handle iMessage.

    Ordre:
    1) relationship_profiles.handle
    2) imessage_analysis_cache + Contacts.resolve_handle
    3) cache Contacts (inverse nom -> handle)
    4) champ people.name si déjà un handle
    5) recherche iMessage directe (LIKE handle / texte)
    """
    from database import get_db, get_person

    key = (name or "").strip()
    if not key:
        logger.info("[resolve] %s -> %s", name, None)
        return None

    person = get_person(key)
    if not person:
        logger.info("[resolve] %s -> %s", key, None)
        return None

    # 1) relationship_profiles.handle
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT handle FROM relationship_profiles WHERE person_id = ?",
                (person["id"],),
            ).fetchone()
            if row and row["handle"]:
                h = str(row["handle"]).strip()
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] relationship_profiles: %s", e)

    # 2) imessage_analysis_cache -> resolve via Contacts
    try:
        from integrations.contacts import contacts_reader

        contacts_reader.build_cache()
        with get_db() as conn:
            rows = conn.execute("SELECT handle FROM imessage_analysis_cache").fetchall()
        for row in rows:
            h = str(row["handle"] or "").strip()
            if not h:
                continue
            resolved = contacts_reader.resolve_handle(h)
            if resolved and resolved.strip().lower() == key.lower():
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] analysis_cache/contacts: %s", e)

    # 3) contacts cache inverse lookup
    try:
        from integrations.contacts import contacts_reader

        contacts_reader.build_cache()
        for handle, contact_name in contacts_reader._cache.items():
            cn = str(contact_name or "").strip().lower()
            if cn != key.lower():
                continue
            h = str(handle).strip()
            if h.startswith("+") or "@" in h or re.match(r"^\d{10,}$", h):
                logger.info("[resolve] %s -> %s", key, h)
                return h
    except Exception as e:
        logger.debug("[resolve] contacts inverse: %s", e)

    # 4) people.name déjà handle
    person_name = (person.get("name") or "").strip()
    if re.match(r"^[\+\d\s\-\.]+$", person_name) or "@" in person_name:
        logger.info("[resolve] %s -> %s", key, person_name)
        return person_name

    # 5) recherche iMessage directe
    try:
        from integrations.imessage_reader import imessage_reader

        if imessage_reader and imessage_reader.is_available():
            msgs = imessage_reader.get_conversation_with(key, limit=5)
            if msgs:
                for m in msgs:
                    if not m.get("is_from_me") and m.get("handle"):
                        h = str(m["handle"]).strip()
                        logger.info("[resolve] %s -> %s", key, h)
                        return h
                h0 = str(msgs[0].get("handle") or "").strip()
                if h0:
                    logger.info("[resolve] %s -> %s", key, h0)
                    return h0
    except Exception as e:
        logger.debug("[resolve] imessage direct: %s", e)

    logger.info("[resolve] %s -> %s", key, None)
    return None


def _format_contact_timeline(timeline: list) -> str:
    lines = []
    for ev in (timeline or [])[:18]:
        dt = ev.get("event_date") or (str(ev.get("created_at") or "")[:16])
        summary = (ev.get("summary") or "").strip()
        et = ev.get("event_type") or ""
        lines.append(f"- [{dt}] ({et}) {summary[:500]}")
    return "\n".join(lines) if lines else "(aucun événement structuré)"


def _format_people_events(events: list) -> str:
    lines = []
    for ev in (events or [])[:12]:
        dt = (str(ev.get("created_at") or ""))[:16]
        content = (ev.get("content") or "").strip()
        et = ev.get("event_type") or ""
        lines.append(f"- [{dt}] ({et}) {content[:400]}")
    return "\n".join(lines) if lines else "(aucun événement people_events)"


def _format_imessage_snippets(msgs: list, contact_label: str) -> str:
    lines = []
    for m in msgs[-35:]:
        who = "Moi" if m.get("is_from_me") else contact_label
        ts = m.get("date_short") or ""
        tx = (m.get("text") or "").replace("\n", " ")[:650]
        lines.append(f"{ts} · {who}: {tx}")
    return "\n".join(lines) if lines else "(aucun extrait iMessage — handle ou chat.db)"


async def _generate_person_ai_description(person: dict, profile: dict | None) -> tuple[str, dict]:
    """Génère une description courte (Haiku) et retourne (texte, meta llm)."""
    rp = profile or {}
    topics = rp.get("topics") or ""
    if isinstance(topics, (list, dict)):
        topics = json.dumps(topics, ensure_ascii=False)
    user_msg = f"""Génère une description concise de {person.get("name")} en 3-4 phrases, du point de vue de {config.USER_NAME}.

Déduis le genre de {person.get("name")} à partir du prénom et du contexte. Utilise les pronoms appropriés (il/elle).

Données :
Relation : {person.get("relationship") or "—"}
Dynamique : {person.get("dynamics") or "—"}
Personnalité : {person.get("personality_notes") or "—"}
Style comm : {rp.get("communication_style") or "—"}
Sentiment : {rp.get("sentiment") or "—"}
Sujets : {topics or "—"}
Fréquence : {rp.get("interaction_frequency") or "—"}

Écris comme un profil humain naturel, pas comme une fiche technique. Pas d'emoji. Français."""
    res = await llm.chat(
        messages=[{"role": "user", "content": user_msg}],
        model=config.DEEPSEEK_FAST_MODEL,
        system="Tu réponds uniquement par le texte de la description, sans titre ni préambule.",
        max_tokens=500,
        temperature=0.4,
        use_cache=False,
    )
    text = (res.get("content") or "").strip()
    text = strip_leading_emotion(text)
    return text, res
