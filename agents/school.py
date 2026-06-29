"""Agent ÉCOLE — cours, résumés, fiches, flashcards, exercices/devoirs.

Particularité : utilise `_route_task()` de BaseAgent qui décide automatiquement
entre Claude (analyse, conversation, fiche courte) et Gemini CLI (devoir complet,
dissertation, code long). Quand Gemini produit un devoir, le prompt système
demande de terminer la réponse par un bloc ```save JSON``` qu'on parse ici pour
sauver le fichier dans data/outputs/school/[matière]/.
"""

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import AsyncGenerator

import config
from agents import BaseAgent
from agents.display_text import finalize_assistant_display_text

logger = logging.getLogger(__name__)

# Bloc à la fin de la réponse quand un devoir est produit. Cf. prompts/school.txt
# Forme attendue :
#   ```save
#   {"action":"save","filename":"...","subject":"...","type":"...","title":"..."}
#   ```
SAVE_BLOCK_RE = re.compile(r"```save\s*\n(.*?)\n```", re.DOTALL)

# Découpage pour pseudo-streaming (Gemini ne stream pas en JSON, on simule)
STREAM_CHUNK_SIZE = 20


class SchoolAgent(BaseAgent):
    """Agent école : Sonnet pour analyse/fiches, Gemini CLI pour devoirs longs."""

    name = "school"
    description = "Agent école — cours, résumés, exercices, devoirs"
    model = config.DEEPSEEK_MAIN_MODEL

    async def handle(self, user_message: str, conversation_id: int = None,
                     context: dict = None) -> dict:
        """Traite un message scolaire — route automatiquement vers Claude ou Gemini.

        Si la réponse contient un bloc ```save JSON```, sauvegarde le devoir
        dans data/outputs/school/[matière]/ et ajoute `saved_file` au résultat.
        """
        result = await self._route_task(user_message, conversation_id, context)
        raw = result.get("response", "")

        if "```save" in raw:
            saved_path = self._save_school_file(raw)
            if saved_path:
                result["saved_file"] = str(saved_path)

        result["response"] = finalize_assistant_display_text(raw)
        return result

    async def handle_stream(self, user_message: str, conversation_id: int = None,
                            context: dict = None) -> AsyncGenerator[dict, None]:
        """Version pseudo-streaming.

        `_route_task` n'expose pas de streaming (Gemini CLI subprocess est lu d'un coup,
        Claude pourrait streamer mais on uniformise). On découpe la réponse complète
        en chunks de STREAM_CHUNK_SIZE caractères pour rester compatible avec le
        WebSocket frontend qui attend des events `{type: chunk}`.
        """
        yield {"type": "classification", "agent": self.name}

        result = await self._route_task(user_message, conversation_id, context)
        raw = result.get("response", "")
        display_text = finalize_assistant_display_text(raw)

        for i in range(0, len(display_text), STREAM_CHUNK_SIZE):
            yield {"type": "chunk", "content": display_text[i:i + STREAM_CHUNK_SIZE]}
            await asyncio.sleep(0.01)

        yield {
            "type": "done",
            "agent": self.name,
            "model": result.get("model"),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "cost": result.get("cost", 0.0),
            "content": display_text,
        }

        if "```save" in raw:
            saved_path = self._save_school_file(raw)
            if saved_path:
                yield {"type": "saved_file", "path": str(saved_path)}

    def _save_school_file(self, response_text: str) -> Path | None:
        """Extrait le bloc ```save JSON``` et écrit le devoir sur disque.

        Le contenu du fichier = tout ce qui précède le bloc ```save (le devoir lui-même).
        Retourne le chemin écrit, ou None si pas de bloc valide.
        """
        match = SAVE_BLOCK_RE.search(response_text)
        if not match:
            return None

        raw_json = match.group(1).strip()
        try:
            meta = json.loads(raw_json)
        except json.JSONDecodeError as e:
            logger.warning(f"[school] Bloc ```save trouvé mais JSON invalide : {e}")
            return None

        filename = meta.get("filename")
        subject = meta.get("subject", "divers")
        if not filename:
            logger.warning(f"[school] Bloc ```save sans 'filename' : {meta}")
            return None

        # Dossier : data/outputs/school/[matière_normalisée]/
        # NFKD enlève les accents (Économie → Economie → economie)
        normalized = unicodedata.normalize("NFKD", subject.lower())
        ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
        subject_slug = re.sub(r"[^a-z0-9]+", "_", ascii_only).strip("_") or "divers"
        out_dir = Path(config.SCHOOL_OUTPUT_DIR) / subject_slug
        out_dir.mkdir(parents=True, exist_ok=True)

        # Contenu du fichier = tout AVANT le bloc ```save
        file_content = response_text[:match.start()].rstrip()

        # En-tête markdown auto si fichier .md (titre humain en plus du nom de fichier)
        title = meta.get("title")
        doc_type = meta.get("type")
        if filename.endswith(".md") and title and not file_content.lstrip().startswith("#"):
            header = f"# {title}\n"
            if doc_type:
                header += f"\n*{doc_type}*\n"
            file_content = f"{header}\n{file_content}"

        filepath = out_dir / filename
        try:
            filepath.write_text(file_content, encoding="utf-8")
        except OSError as e:
            logger.error(f"[school] Échec écriture {filepath} : {e}")
            return None

        logger.info(f"[school] Fichier sauvé : {filepath}")
        return filepath


school_agent = SchoolAgent()
