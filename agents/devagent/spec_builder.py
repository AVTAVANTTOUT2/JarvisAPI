"""Generation et verrouillage de spec.json pour projets DevAgent."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import config
from agents.devagent.models import DevSpec
from agents.devagent.utils import slugify

logger = logging.getLogger(__name__)

DEV_PROJECTS_ROOT = Path(config.DEV_PROJECTS_ROOT)


def build_isolation_path(slug: str) -> Path:
    """Cree l'arborescence isolee d'un projet."""
    path = DEV_PROJECTS_ROOT / slug
    path.mkdir(parents=True, exist_ok=True)
    (path / "src").mkdir(exist_ok=True)
    return path


def lock_spec(spec_dict: dict) -> DevSpec:
    """Valide, enrichit et ecrit spec.json sur disque."""
    project_name = spec_dict.get("project_name") or spec_dict.get("name") or "project"
    slug = spec_dict.get("slug") or slugify(str(project_name))
    spec_dict["slug"] = slug
    spec_dict["project_name"] = project_name

    isolation = build_isolation_path(slug)
    spec_dict["isolation_path"] = str(isolation)

    if "loop_budget" not in spec_dict or not isinstance(spec_dict["loop_budget"], dict):
        spec_dict["loop_budget"] = {
            "max_iterations": 25,
            "max_tokens": 500_000,
            "max_consecutive_failures": 3,
        }

    spec = DevSpec(**spec_dict)
    spec_path = isolation / "spec.json"
    spec_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

    state_path = isolation / ".devagent_state.json"
    if not state_path.exists():
        state_path.write_text(
            json.dumps({"phase": "init", "iteration": 0}, indent=2),
            encoding="utf-8",
        )

    logger.info("[devagent] spec verrouillee slug=%s path=%s", slug, isolation)
    return spec
