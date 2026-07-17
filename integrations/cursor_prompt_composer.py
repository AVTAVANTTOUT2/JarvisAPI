"""Compose des prompts Cursor versionnés à partir des templates JARVIS."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(config.BASE_DIR) / "prompts" / "cursor"

RESULT_MARKER_BEGIN = "JARVIS_CURSOR_RESULT_BEGIN"
RESULT_MARKER_END = "JARVIS_CURSOR_RESULT_END"


@dataclass
class PromptTemplate:
    template_id: str
    version: str
    domain: str
    path: Path
    body: str
    variables: list[str]

    def render(self, values: dict[str, Any]) -> str:
        out = self.body
        for key, val in values.items():
            out = out.replace("{{" + key + "}}", str(val if val is not None else ""))
        # Variables manquantes → chaîne vide
        out = re.sub(r"\{\{[a-zA-Z0-9_]+\}\}", "", out)
        return out


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta_raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in meta_raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def load_template(template_id: str) -> PromptTemplate:
    path = PROMPTS_DIR / f"{template_id}.md"
    if not path.is_file():
        path = PROMPTS_DIR / "feature_implementation.md"
        template_id = "feature_implementation"
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)
    variables = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", body)
    return PromptTemplate(
        template_id=meta.get("id", template_id),
        version=meta.get("version", "1.0.0"),
        domain=meta.get("domain", "general"),
        path=path,
        body=body,
        variables=sorted(set(variables)),
    )


async def compose_cursor_prompt(
    *,
    user_request: str,
    template_id: str,
    acceptance_criteria: list[str] | None = None,
    required_tests: list[str] | None = None,
    context_files: list[str] | None = None,
    extra_context: str = "",
    use_main_model: bool = True,
) -> dict[str, Any]:
    """Construit le prompt final. DeepSeek Main affine si disponible."""
    tmpl = load_template(template_id)
    criteria = acceptance_criteria or [
        "Les tests pertinents passent",
        "Aucun changement hors périmètre",
        "Rapport final présent avec marqueurs JARVIS_CURSOR_RESULT",
    ]
    tests = required_tests or ["pytest tests/ -q --tb=line -x"]
    values = {
        "user_request": user_request,
        "acceptance_criteria": "\n".join(f"- {c}" for c in criteria),
        "required_tests": "\n".join(f"- {t}" for t in tests),
        "context_files": "\n".join(f"- {f}" for f in (context_files or [])) or "(Cursor inspecte le dépôt)",
        "extra_context": extra_context or "(aucun)",
        "repo_rules": (
            "- Ne jamais modifier main directement\n"
            "- Travailler uniquement dans le worktree / branche fournie\n"
            "- Ne pas lire ni inclure de fichiers .env ou secrets\n"
            "- Tester avant de déclarer terminé\n"
            "- Préserver les contrats API existants"
        ),
        "result_format": (
            f"{RESULT_MARKER_BEGIN}\n"
            "Verdict: COMPLETED | PARTIAL | BLOCKED\n"
            "Root cause:\n...\n"
            "Files changed:\n...\n"
            "Tests:\n...\n"
            "Runtime validation:\n...\n"
            "Git:\n...\n"
            "PR:\n...\n"
            "Remaining risks:\n...\n"
            f"{RESULT_MARKER_END}"
        ),
        "date": date.today().isoformat(),
        "template_version": tmpl.version,
    }
    base_prompt = tmpl.render(values)

    refined = base_prompt
    composer_model = (
        getattr(config, "MAIN_REASONING_MODEL", None) or config.DEEPSEEK_MAIN_MODEL
        if use_main_model
        else config.DEEPSEEK_FAST_MODEL
    )
    try:
        import llm

        result = await llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Tu es l'architecte de prompts pour Cursor CLI. "
                        "Améliore le prompt suivant pour qu'il soit précis, testable et sans ambiguïté. "
                        "Conserve les marqueurs JARVIS_CURSOR_RESULT_BEGIN/END. "
                        "Ne rajoute aucun secret. Réponds UNIQUEMENT avec le prompt final.\n\n"
                        f"{base_prompt}"
                    ),
                }
            ],
            model=composer_model,
            system="Tu produis des prompts d'ingénierie de qualité production.",
            max_tokens=4096,
            temperature=0.2,
        )
        refined = (result.get("content") or base_prompt).strip()
    except Exception as exc:
        logger.warning("[cursor_prompt] composition DeepSeek skip: %s", exc)

    return {
        "prompt": refined,
        "template_id": tmpl.template_id,
        "template_version": tmpl.version,
        "composer_model": composer_model,
        "base_prompt": base_prompt,
    }


def parse_cursor_result(raw_output: str) -> dict[str, Any]:
    """Parse la section structurée du rapport Cursor."""
    if not raw_output:
        return {"verdict": "BLOCKED", "raw": "", "parsed": False, "error": "sortie vide"}
    begin = raw_output.find(RESULT_MARKER_BEGIN)
    end = raw_output.find(RESULT_MARKER_END)
    if begin < 0 or end < 0 or end <= begin:
        return {
            "verdict": "PARTIAL",
            "raw": raw_output,
            "parsed": False,
            "error": "marqueurs JARVIS_CURSOR_RESULT absents",
            "body": raw_output[-4000:],
        }
    body = raw_output[begin + len(RESULT_MARKER_BEGIN) : end].strip()
    fields: dict[str, str] = {}
    current_key = None
    buf: list[str] = []
    for line in body.splitlines():
        if re.match(r"^[A-Za-z][A-Za-z ]+:\s*", line) and not line.startswith(" "):
            if current_key:
                fields[current_key] = "\n".join(buf).strip()
            key, _, rest = line.partition(":")
            current_key = key.strip().lower().replace(" ", "_")
            buf = [rest.strip()] if rest.strip() else []
        else:
            buf.append(line)
    if current_key:
        fields[current_key] = "\n".join(buf).strip()
    verdict = (fields.get("verdict") or "PARTIAL").strip().upper()
    if verdict not in {"COMPLETED", "PARTIAL", "BLOCKED"}:
        verdict = "PARTIAL"
    return {
        "verdict": verdict,
        "raw": raw_output,
        "parsed": True,
        "fields": fields,
        "body": body,
    }
