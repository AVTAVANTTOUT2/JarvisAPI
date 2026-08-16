"""Points d'entrée des connecteurs vers la détection de tâches.

Ces fonctions sont le **seul** pont entre un connecteur (Mail, iMessage, voix)
et le domaine. Elles sont volontairement minces et sans état : le connecteur
décide quoi lire — c'est sa responsabilité et son autorisation —, le domaine
décide s'il y a une demande, et personne ici ne peut répondre, envoyer ou
exécuter quoi que ce soit.

Chaque fonction avale ses erreurs et retourne `None` : une détection ratée ne
doit jamais casser la boucle du watcher qui l'appelle.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger("jarvis")


def _detection_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "TASK_DETECTION_ENABLED", True))
    except Exception:
        return False


async def ingest_email_for_detection(
    message: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Analyse un e-mail déjà lu et autorisé par le watcher.

    Retourne un petit résumé de ce qui a été créé, ou `None` si rien. Ne
    répond jamais à l'e-mail et ne déclenche aucune exécution.
    """

    if not _detection_enabled():
        return None
    try:
        from .detection import detection_input_from_email, detector_from_config
        from .service import get_task_control_service

        detector = detector_from_config()
        payload = detection_input_from_email(message)
        detections = detector.detect(payload)
        if not detections:
            return None
        service = get_task_control_service()
        created: list[str] = []
        candidates: list[str] = []
        for detected in detections:
            candidate, task = await service.ingest_detection(detected)
            if task is not None:
                created.append(task.task_id)
            elif candidate is not None:
                candidates.append(candidate.candidate_id)
        logger.info(
            "[task_detection] e-mail : %d tâche(s), %d candidat(s)",
            len(created),
            len(candidates),
        )
        return {"tasks": created, "candidates": candidates}
    except Exception:
        logger.exception("[task_detection] analyse e-mail impossible")
        return None


async def ingest_message_for_detection(
    message: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Même contrat pour un message reçu. Ne répond jamais au contact."""

    if not _detection_enabled():
        return None
    try:
        from .detection import detection_input_from_message, detector_from_config
        from .service import get_task_control_service

        detector = detector_from_config()
        payload = detection_input_from_message(message)
        detections = detector.detect(payload)
        if not detections:
            return None
        service = get_task_control_service()
        created: list[str] = []
        candidates: list[str] = []
        for detected in detections:
            candidate, task = await service.ingest_detection(detected)
            if task is not None:
                created.append(task.task_id)
            elif candidate is not None:
                candidates.append(candidate.candidate_id)
        logger.info(
            "[task_detection] message : %d tâche(s), %d candidat(s)",
            len(created),
            len(candidates),
        )
        return {"tasks": created, "candidates": candidates}
    except Exception:
        logger.exception("[task_detection] analyse message impossible")
        return None


async def create_task_from_user_request(
    request: str,
    *,
    channel: str = "voice",
    conversation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    planning_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Crée une tâche durable depuis une demande adressée à JARVIS.

    C'est le chemin « JARVIS, prépare-moi… » : la demande devient une tâche
    **planifiée**, pas une tâche lancée. La phrase de retour le dit
    explicitement, pour que l'utilisateur sache qu'une validation l'attend.
    """

    try:
        from .models import TaskSource, TaskSourceChannel, TaskSourceType
        from .service import get_task_control_service

        try:
            source_channel = TaskSourceChannel(channel)
        except ValueError:
            source_channel = TaskSourceChannel.API
        service = get_task_control_service()
        task = await service.create_task(
            title=request,
            description=request,
            source=TaskSource(
                source_type=TaskSourceType.USER_REQUEST,
                channel=source_channel,
                reference=f"conversation:{conversation_id}" if conversation_id else "",
            ),
            conversation_id=conversation_id,
            metadata=metadata,
            planning_context=planning_context,
            autoplan=True,
        )
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "spoken": (
                "Je prépare un plan. Il attendra votre validation avant tout démarrage."
            ),
        }
    except Exception:
        logger.exception("[task_detection] création depuis demande impossible")
        return None


__all__ = [
    "create_task_from_user_request",
    "ingest_email_for_detection",
    "ingest_message_for_detection",
]
