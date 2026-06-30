"""Analyseur relationnel — DeepSeek (modèle rapide) extrait des données structurées des conversations iMessage.

Architecture 2 tiers :
  1. DeepSeek rapide lit les messages bruts → JSON structuré → stocké dans la DB (~$0.002/analyse)
  2. DeepSeek principal reçoit les données structurées de la DB, jamais les messages bruts

Workers :
  - run_initial_scan() : première analyse complète de tout l'historique
  - run_daily_update() : analyse incrémentale des nouveaux messages
  - analyze_single_contact(name) : analyse à la demande
"""

import json
import logging
import re
from pathlib import Path

import config
import llm
from database import (
    add_cross_insight,
    add_fact,
    add_relationship_event,
    find_or_create_pattern,
    get_analysis_cursor,
    get_person,
    get_total_messages_analyzed,
    rename_person_if_phone_number,
    sync_imessage_counts_to_people,
    update_analysis_cursor,
    update_person_imessage_count,
    upsert_person,
    upsert_relationship_profile,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
BATCH_SIZE = 50
MIN_MESSAGES_FOR_ANALYSIS = 10
JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def _load_extractor_prompt() -> str:
    path = PROMPTS_DIR / "imessage_extractor.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    logger.error("[analyzer] prompts/imessage_extractor.txt introuvable")
    return ""


def _format_messages_for_prompt(messages: list[dict], user_name: str) -> str:
    """Formate les messages iMessage pour le prompt extracteur."""
    lines = []
    for m in messages:
        who = "MOI" if m.get("is_from_me") else "CONTACT"
        date_str = m.get("date_short", "?")
        text = (m.get("text") or "").replace("\n", " ").strip()
        if text:
            lines.append(f"[{date_str}] {who}: {text}")
    return "\n".join(lines)


class RelationshipAnalyzer:
    """Analyse les conversations iMessage via DeepSeek rapide et stocke les données en DB."""

    def __init__(self):
        self._prompt_template = ""

    def _get_prompt(self) -> str:
        if not self._prompt_template:
            self._prompt_template = _load_extractor_prompt()
        return self._prompt_template

    async def run_initial_scan(self) -> dict:
        """Première analyse complète de l'historique iMessage."""
        from integrations.imessage_reader import imessage_reader

        logger.info("[analyzer] Démarrage du scan initial iMessage…")

        if not imessage_reader.is_available():
            logger.warning("[analyzer] iMessage non disponible — scan annulé")
            return {"status": "unavailable"}

        contacts = imessage_reader.get_all_contacts()
        logger.info("[analyzer] %d contacts trouvés dans chat.db (handles uniques)", len(contacts))

        stats = {"contacts_scanned": 0, "batches_processed": 0, "errors": 0}

        for contact in contacts:
            handle = contact["handle"]
            msg_count = contact["msg_count"]

            if msg_count < MIN_MESSAGES_FOR_ANALYSIS:
                logger.debug(
                    "[analyzer] Skip %s — seulement %d messages (< %d)",
                    handle,
                    msg_count,
                    MIN_MESSAGES_FOR_ANALYSIS,
                )
                continue

            cursor = get_analysis_cursor(handle)
            if cursor > 0:
                logger.info("[analyzer] %s déjà analysé (cursor=%d) — skip initial", handle, cursor)
                continue

            try:
                logger.info(
                    "[analyzer] Analyse initiale de %s — %d messages",
                    handle,
                    msg_count,
                )
                await self._analyze_contact_full(handle)
                stats["contacts_scanned"] += 1
            except Exception as e:
                logger.error("[analyzer] Erreur scan %s : %s", handle, e)
                stats["errors"] += 1

        logger.info("[analyzer] Scan initial terminé : %s", stats)
        # Synchroniser les compteurs de messages vers la table people
        synced = sync_imessage_counts_to_people()
        if synced:
            logger.info("[analyzer] imessage_count synchronisé pour %d contacts", synced)
        return stats

    async def run_daily_update(self) -> dict:
        """Analyse incrémentale : nouveaux messages depuis le dernier cursor."""
        from integrations.imessage_reader import imessage_reader

        if not imessage_reader.is_available():
            return {"status": "unavailable"}

        contacts = imessage_reader.get_all_contacts()
        stats = {"contacts_updated": 0, "batches_processed": 0}

        for contact in contacts:
            handle = contact["handle"]
            cursor = get_analysis_cursor(handle)

            messages = imessage_reader.get_conversation(handle, limit=200, since_rowid=cursor)
            if len(messages) < 5:
                continue

            try:
                batches = await self._process_in_batches(handle, messages)
                stats["batches_processed"] += batches
                stats["contacts_updated"] += 1
            except Exception as e:
                logger.error("[analyzer] Daily update %s : %s", handle, e)

        logger.info("[analyzer] Update quotidien terminé : %s", stats)
        # Synchroniser les compteurs de messages vers la table people
        synced = sync_imessage_counts_to_people()
        if synced:
            logger.info("[analyzer] imessage_count synchronisé pour %d contacts", synced)
        return stats

    async def analyze_single_contact(self, name_or_handle: str) -> dict | None:
        """Analyse à la demande d'un contact spécifique."""
        from integrations.imessage_reader import imessage_reader

        if not imessage_reader.is_available():
            return None

        messages = imessage_reader.get_conversation_with(name_or_handle, limit=200)
        if not messages:
            logger.info("[analyzer] Aucun message trouvé pour '%s'", name_or_handle)
            return None

        # Trouver le handle réel
        handle = None
        contacts = imessage_reader.get_all_contacts()
        q = name_or_handle.lower()
        for c in contacts:
            if q in c["handle"].lower():
                handle = c["handle"]
                break

        if not handle:
            handle = name_or_handle

        batches = await self._process_in_batches(handle, messages)
        logger.info("[analyzer] Analyse de '%s' terminée (%d batches)", name_or_handle, batches)

        person = get_person(name_or_handle)
        return person

    async def _analyze_contact_full(self, handle: str) -> int:
        """Analyse complète d'un contact (tous les messages en batches)."""
        from integrations.imessage_reader import imessage_reader

        all_messages = imessage_reader.get_conversation(handle, limit=2000, since_rowid=0)
        if len(all_messages) < MIN_MESSAGES_FOR_ANALYSIS:
            return 0

        return await self._process_in_batches(handle, all_messages)

    async def _process_in_batches(self, handle: str, messages: list[dict]) -> int:
        """Découpe en batches et analyse chaque batch."""
        batches_done = 0

        for i in range(0, len(messages), BATCH_SIZE):
            batch = messages[i:i + BATCH_SIZE]
            if not batch:
                continue

            try:
                await self._analyze_batch(handle, batch)
                batches_done += 1

                last_rowid = max(m["rowid"] for m in batch)
                update_analysis_cursor(handle, last_rowid, len(batch))
            except Exception as e:
                logger.error("[analyzer] Batch %s[%d:%d] : %s", handle, i, i + len(batch), e)

        return batches_done

    async def _analyze_batch(self, handle: str, messages: list[dict]) -> None:
        """Envoie un batch à DeepSeek et stocke les résultats en DB."""
        prompt_template = self._get_prompt()
        if not prompt_template:
            return

        formatted = _format_messages_for_prompt(messages, config.USER_NAME)
        prompt = prompt_template.replace("{{user_name}}", config.USER_NAME)
        prompt = prompt.replace("{{handle}}", handle)
        prompt = prompt.replace("{{messages}}", formatted)

        try:
            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=config.DEEPSEEK_FAST_MODEL,
                system="Tu es un extracteur de données. Retourne UNIQUEMENT du JSON valide.",
                max_tokens=2000,
                temperature=0.1,
            )
        except Exception as e:
            logger.error("[analyzer] LLM call : %s", e)
            return

        response = result.get("content", "")
        data = self._parse_json(response)
        if not data:
            return

        self._store_results(handle, data)
        logger.info(
            "[analyzer] %s : batch %d msgs analysé (cost=$%.4f)",
            handle, len(messages), result.get("cost", 0),
        )

    def _parse_json(self, response: str) -> dict | None:
        match = JSON_BLOCK_RE.search(response)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Fallback : chercher un JSON brut
        try:
            start = response.index("{")
            end = response.rindex("}") + 1
            return json.loads(response[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.warning("[analyzer] JSON invalide dans la réponse DeepSeek")
            return None

    def _store_results(self, handle: str, data: dict, msg_count: int = 0) -> None:
        """Stocke les résultats de l'analyse en DB."""
        person_data = data.get("person") or {}
        likely = (person_data.get("likely_name") or "").strip()
        
        # Détermine le nom initial (peut être un numéro)
        initial_name = handle
        try:
            from integrations.contacts import contacts_reader
            if contacts_reader.is_available():
                resolved = contacts_reader.resolve_handle(handle)
                if resolved:
                    initial_name = resolved
        except ImportError:
            pass
        
        # Utilise likely_name si disponible, sinon le nom résolu/handle
        name = likely if likely else initial_name

        # 1. Upsert person
        person_id = upsert_person(
            name,
            relationship=person_data.get("relationship_guess"),
            personality_notes=person_data.get("communication_style"),
            dynamics=person_data.get("power_dynamic"),
        )

        # 1b. Update imessage_count
        if msg_count > 0:
            update_person_imessage_count(person_id, msg_count)

        # 1c. Si le nom initial était un numéro et qu'on a un likely_name meilleur, renommer
        # (utile pour les contacts déjà créés avec un numéro)
        if likely and re.match(r'^[\+\d\s\-\.]+$', initial_name):
            if rename_person_if_phone_number(person_id, likely):
                logger.info("[analyzer] Renamed phone %s → %s", initial_name, likely)

        # 2. Upsert relationship profile
        profile_kwargs = {}
        if person_data.get("communication_style"):
            profile_kwargs["communication_style"] = person_data["communication_style"]
        if person_data.get("response_speed"):
            profile_kwargs["response_pattern"] = person_data["response_speed"]
        if person_data.get("topics"):
            profile_kwargs["topics"] = json.dumps(person_data["topics"])
        if person_data.get("sentiment"):
            profile_kwargs["sentiment"] = person_data["sentiment"]
        if person_data.get("power_dynamic"):
            profile_kwargs["power_dynamic"] = person_data["power_dynamic"]
        if person_data.get("trust_level"):
            profile_kwargs["trust_level"] = person_data["trust_level"]
        if handle:
            profile_kwargs["handle"] = handle

        if profile_kwargs:
            upsert_relationship_profile(person_id, **profile_kwargs)

        # 3. Facts about user
        for fact in data.get("facts_about_user") or []:
            if not isinstance(fact, dict) or not fact.get("content"):
                continue
            try:
                add_fact(
                    category=fact.get("category", "memory"),
                    content=fact["content"],
                    source="imessage",
                    confidence=fact.get("confidence", "medium"),
                )
            except Exception as e:
                logger.error("[analyzer] add_fact : %s", e)

        # 4. Notable events
        for event in data.get("notable_events") or []:
            if not isinstance(event, dict) or not event.get("summary"):
                continue
            try:
                add_relationship_event(
                    person_id=person_id,
                    event_type=event.get("type", "milestone"),
                    summary=event["summary"],
                    event_date=event.get("date_approx"),
                    impact_on_user=event.get("impact_on_user"),
                    source="imessage",
                )
            except Exception as e:
                logger.error("[analyzer] add_event : %s", e)

        # 5. Patterns observed
        for pattern in data.get("patterns_observed") or []:
            if not isinstance(pattern, dict) or not pattern.get("description"):
                continue
            try:
                find_or_create_pattern(
                    pattern["description"],
                    pattern_type=pattern.get("type", "relational"),
                )
            except Exception as e:
                logger.error("[analyzer] pattern : %s", e)


analyzer = RelationshipAnalyzer()
