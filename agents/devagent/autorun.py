"""Agent autonome multi-fichiers — zéro intervention humaine.

``devagent_start`` + ``devagent_answer`` (répétés) + ``devagent_run`` sont les
trois étapes manuelles existantes. ``autorun_project`` les enchaîne seul :
une IA "product owner" (qui connaît la description originale de la demande)
répond à sa place aux questions de l'interview, jusqu'à ce que la spec soit
verrouillée, puis la boucle plan → code → test → fix → commit démarre
immédiatement. Le tout sans qu'un humain ne valide quoi que ce soit entre
les deux — la demande initiale EST la validation.

Garde-fou : ``DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS`` borne le nombre de
questions avant de forcer la finalisation (au cas où l'IA-intervieweuse ne
convergerait jamais), pour ne jamais boucler indéfiniment sur des tokens.
"""

from __future__ import annotations

import logging

import config
from agents.devagent.interview import next_interview_step, submit_answer
from agents.devagent.loop import run_loop
from agents.devagent.spec_builder import build_isolation_path, lock_spec
from agents.devagent.utils import parse_json_response, slugify
from database import devagent as devagent_db
from integrations.deepseek_client import call_deepseek

logger = logging.getLogger(__name__)

AUTO_ANSWER_PROMPT = """Role: product owner qui répond à l'interview d'un tech lead
pour SON PROPRE projet, dont voici la demande d'origine :

\"\"\"{description}\"\"\"

Le tech lead pose cette question :
{question}
Type de réponse attendu : {qtype}
{options_block}

Réponds à la question de façon cohérente avec la demande d'origine, précise
et actionnable, comme le ferait la personne qui a formulé cette demande.
Retourne UNIQUEMENT ce JSON : {{"answer": "ta réponse"}}
"""


async def _auto_answer(description: str, next_question: dict) -> str:
    """Génère la réponse d'interview à la place de l'humain, via DeepSeek."""
    options = next_question.get("options") or []
    options_block = f"Options proposées : {options}" if options else ""
    response = await call_deepseek(
        system=AUTO_ANSWER_PROMPT.format(
            description=description,
            question=next_question.get("question", ""),
            qtype=next_question.get("type", "text"),
            options_block=options_block,
        ),
        user="Réponds à la question.",
        json_mode=True,
    )
    payload = parse_json_response(response["content"])
    answer = payload.get("answer")
    if not answer:
        raise RuntimeError("Auto-réponse vide — impossible de poursuivre l'interview sans intervention.")
    return str(answer)


async def autorun_project(description: str, name: str | None = None) -> dict:
    """Crée un projet DevAgent et l'implémente de bout en bout, sans intervention.

    Retourne ``{project_id, slug, spec, interview_rounds}`` dès que la spec
    est verrouillée et que la boucle a démarré (la boucle elle-même tourne
    en tâche de fond — suivre l'avancement via
    ``devagent_db.get_project_status_payload``).
    """
    project_name = name or (description[:60].strip() or "Projet autonome")
    slug = slugify(project_name)
    if devagent_db.get_project_by_slug(slug):
        # Évite une collision silencieuse : suffixe déterministe basé sur la description.
        import hashlib

        suffix = hashlib.sha256(description.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug}-{suffix}"
        project_name = f"{project_name} ({suffix})"

    isolation = str(build_isolation_path(slug))
    project_id = devagent_db.create_dev_project(slug, project_name, isolation)

    context: dict = {"qa_history": [], "original_description": description}
    # La première "réponse" au tech lead EST la demande d'origine — pas une
    # question de l'interview, mais le point de départ du contexte.
    context["qa_history"].append({"q": "Décris ta demande.", "a": description})

    rounds = 0
    result = await next_interview_step(project_id, context)
    while not result.get("done") and rounds < config.DEVAGENT_AUTORUN_MAX_INTERVIEW_ROUNDS:
        answer = await _auto_answer(description, result)
        result = await submit_answer(project_id, result.get("question", ""), answer, context)
        devagent_db.save_interview_context(project_id, context)
        rounds += 1

    if not result.get("done"):
        # Force la finalisation : on redemande explicitement la spec, sans
        # nouvelle question — la boucle plan/code affinera le reste.
        context["qa_history"].append({
            "q": "Assez d'informations recueillies — finalise la spec maintenant.",
            "a": "Oui, verrouille la spec avec ce qui est disponible.",
        })
        result = await next_interview_step(project_id, context)
        rounds += 1
        if not result.get("done"):
            devagent_db.update_project_status(project_id, "failed")
            raise RuntimeError(
                f"Interview auto n'a pas convergé après {rounds} tours — "
                "spec non verrouillée, projet marqué en échec."
            )

    spec_dict = result.get("spec")
    if not isinstance(spec_dict, dict):
        devagent_db.update_project_status(project_id, "failed")
        raise RuntimeError("Spec DeepSeek invalide à la finalisation de l'auto-interview.")

    spec = lock_spec(spec_dict)
    devagent_db.save_spec(project_id, spec.model_dump_json())
    devagent_db.complete_interview_session(project_id)
    devagent_db.save_interview_context(project_id, context)
    devagent_db.update_project_status(project_id, "spec_locked")

    logger.info(
        "[devagent-autorun] spec verrouillée en %d tour(s) — démarrage boucle autonome slug=%s",
        rounds, slug,
    )
    devagent_db.update_project_status(project_id, "running")
    import asyncio

    asyncio.create_task(run_loop(project_id), name=f"devagent-autorun-{slug}")

    return {
        "project_id": project_id, "slug": slug,
        "spec": spec.model_dump(), "interview_rounds": rounds,
    }
