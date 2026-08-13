"""Catalogue, routage et confinement des profils de capacités JARVIS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import config
import database
from jarvis.agentic.models import AgenticRequestCategory, AgenticRun
from jarvis.agentic.profiles import (
    CAPABILITY_PROFILE_CONTEXT_KEY,
    CAPABILITY_PROFILES,
    get_capability_profile,
    select_capability_profile,
)
from jarvis.agentic.service import AgenticService


@pytest.fixture
def agentic_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "profiles.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


def test_catalog_contains_exactly_the_eight_minimal_profiles() -> None:
    assert set(CAPABILITY_PROFILES) == {
        "readonly-research",
        "coding",
        "communication",
        "browser",
        "invoice",
        "obs",
        "media",
        "desktop",
    }
    assert "workspace:write" not in CAPABILITY_PROFILES["readonly-research"].permissions
    assert "shell:unrestricted" not in CAPABILITY_PROFILES["coding"].permissions
    assert "git:push" not in CAPABILITY_PROFILES["coding"].permissions
    assert "financial:act" not in CAPABILITY_PROFILES["invoice"].permissions
    assert "privilege:elevate" not in CAPABILITY_PROFILES["desktop"].permissions
    assert CAPABILITY_PROFILES["communication"].approval_permissions == (
        "communications:send",
    )
    assert CAPABILITY_PROFILES["obs"].approval_permissions == ("stream:public:start",)
    assert CAPABILITY_PROFILES["media"].approval_permissions == ("media:publish",)


@pytest.mark.parametrize(
    ("request_text", "category", "expected"),
    [
        (
            "Recherche puis résume ces documents",
            AgenticRequestCategory.AGENTIC_READONLY,
            "readonly-research",
        ),
        (
            "Corrige le code dans le worktree et lance les tests",
            AgenticRequestCategory.AGENTIC_REVERSIBLE,
            "coding",
        ),
        (
            "Prépare un brouillon de message pour ce contact",
            AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
            "communication",
        ),
        (
            "Ouvre ce site web dans le navigateur",
            AgenticRequestCategory.WORKFLOW,
            "browser",
        ),
        (
            "Télécharge la facture fournisseur puis lance l'OCR",
            AgenticRequestCategory.WORKFLOW,
            "invoice",
        ),
        (
            "Configure la scène OBS puis démarre le live public",
            AgenticRequestCategory.AGENTIC_EXTERNAL_EFFECT,
            "obs",
        ),
        (
            "Utilise ffprobe puis crée la preview vidéo",
            AgenticRequestCategory.WORKFLOW,
            "media",
        ),
        (
            "Pilote cette app macOS avec AppleScript",
            AgenticRequestCategory.WORKFLOW,
            "desktop",
        ),
    ],
)
def test_router_selects_each_profile(
    request_text: str,
    category: AgenticRequestCategory,
    expected: str,
) -> None:
    assert select_capability_profile(request_text, category).profile_id == expected


def test_configured_route_can_only_choose_a_compatible_known_profile() -> None:
    selected = select_capability_profile(
        "Workflow générique",
        AgenticRequestCategory.WORKFLOW,
        route_overrides={"workflow": "media"},
    )
    assert selected.profile_id == "media"

    with pytest.raises(ValueError, match="incompatible"):
        select_capability_profile(
            "Analyse seulement",
            AgenticRequestCategory.AGENTIC_READONLY,
            route_overrides={"agentic_readonly": "coding"},
        )
    with pytest.raises(ValueError, match="inconnu"):
        get_capability_profile("runtime-selected-profile")


@pytest.mark.asyncio
async def test_service_persists_distinct_profile_and_refuses_elevation(
    agentic_db: Path,
) -> None:
    service = AgenticService()
    run = await service.create_run(
        title="Corrige le dépôt",
        capability_profile_id="coding",
        permissions=("workspace:read", "workspace:write", "tests:run"),
        selected_context={CAPABILITY_PROFILE_CONTEXT_KEY: "desktop"},
        category=AgenticRequestCategory.AGENTIC_REVERSIBLE,
    )

    assert run.profile_id == "default"
    assert run.selected_context[CAPABILITY_PROFILE_CONTEXT_KEY] == "coding"
    assert run.permissions == get_capability_profile("coding").default_permissions

    with pytest.raises(PermissionError, match="hors du profil"):
        await service.create_run(
            title="Tente une élévation",
            capability_profile_id="readonly-research",
            permissions=("workspace:read", "workspace:write"),
            category=AgenticRequestCategory.AGENTIC_READONLY,
        )
    with pytest.raises(ValueError, match="incompatible"):
        await service.create_run(
            title="Analyse en lecture seule",
            capability_profile_id="coding",
            permissions=("workspace:read",),
            category=AgenticRequestCategory.AGENTIC_READONLY,
        )
    await service.dispose()


@pytest.mark.asyncio
async def test_start_fails_before_runtime_for_permission_outside_router_profile(
    agentic_db: Path,
) -> None:
    service = AgenticService()
    run = await service.create_run(
        title="Permission forgée",
        permissions=("admin:all",),
        category=AgenticRequestCategory.AGENTIC_READONLY,
    )

    failed = await service.start_run(run.run_id)

    assert failed.status.value == "failed"
    assert failed.error is not None
    assert failed.error.code.value == "permission_denied"
    assert failed.provider_session_id is None
    await service.dispose()


@pytest.mark.asyncio
async def test_conversation_router_passes_selected_profile_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    captured: dict[str, Any] = {}

    class _Service:
        def resolve_runtime_id(self, runtime_id: str | None) -> str | None:
            return runtime_id or "fake-runtime"

        async def create_and_start(self, **kwargs: Any) -> AgenticRun:
            captured.update(kwargs)
            return AgenticRun.new(
                profile_id=kwargs["profile_id"],
                origin=kwargs["origin"],
                channel=kwargs["channel"],
                runtime_id=kwargs["runtime_id"],
                title=kwargs["title"],
                conversation_id=kwargs["conversation_id"],
                permissions=kwargs["permissions"],
                selected_context=kwargs["selected_context"],
                category=kwargs["category"],
            )

    monkeypatch.setattr(config, "AGENTIC_RUNTIME", "fake-runtime")
    monkeypatch.setattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")
    monkeypatch.setattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
    monkeypatch.setattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {})
    monkeypatch.setattr(agentic_processing, "get_agentic_service", _Service)

    response = await agentic_processing.maybe_start_agentic_run(
        "/agent Corrige le code puis lance les tests",
        42,
        channel="web",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert captured["capability_profile_id"] == "coding"
    assert (
        captured["permissions"] == get_capability_profile("coding").default_permissions
    )
    assert response["routing"]["capability_profile"] == "coding"


def _capture_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from api import agentic_processing

    captured: dict[str, Any] = {}

    class _Service:
        def resolve_runtime_id(self, runtime_id: str | None) -> str | None:
            return runtime_id or "fake-runtime"

        async def create_and_start(self, **kwargs: Any) -> AgenticRun:
            captured.update(kwargs)
            return AgenticRun.new(
                profile_id=kwargs["profile_id"],
                origin=kwargs["origin"],
                channel=kwargs["channel"],
                runtime_id=kwargs["runtime_id"],
                title=kwargs["title"],
                conversation_id=kwargs["conversation_id"],
                permissions=kwargs["permissions"],
                selected_context=kwargs["selected_context"],
                category=kwargs["category"],
                workspace=kwargs.get("workspace"),
            )

    monkeypatch.setattr(config, "AGENTIC_RUNTIME", "fake-runtime")
    monkeypatch.setattr(config, "AGENTIC_RUNTIME_FALLBACK", "disabled")
    monkeypatch.setattr(config, "AGENTIC_DEFAULT_PROFILE", "readonly-research")
    monkeypatch.setattr(config, "AGENTIC_PROFILE_ROUTE_OVERRIDES", {})
    monkeypatch.setattr(agentic_processing, "get_agentic_service", _Service)
    return captured


@pytest.mark.asyncio
async def test_natural_language_tech_task_starts_coding_run_without_slash_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    captured = _capture_service(monkeypatch)
    response = await agentic_processing.maybe_start_agentic_run(
        "Corrige le bug de connexion Android dans le projet",
        42,
        channel="chat",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert captured["capability_profile_id"] == "coding"
    assert "workspace:write" in captured["permissions"]
    assert captured["category"] is AgenticRequestCategory.AGENTIC_REVERSIBLE


@pytest.mark.asyncio
async def test_html_todolist_on_desktop_starts_coding_run_in_desktop_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from api import agentic_processing
    from jarvis.agentic.desktop_workspace import resolve_desktop_workspace

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    captured = _capture_service(monkeypatch)
    prompt = (
        "jarvis dans le bureau de mon mac crée une todolist appelé todojarvis "
        "je la veut en html css js 3 fichier max hors ligne pas extravagante."
    )
    monkeypatch.setattr(
        agentic_processing,
        "resolve_desktop_workspace",
        lambda text: resolve_desktop_workspace(text, home=tmp_path),
    )

    response = await agentic_processing.maybe_start_agentic_run(
        prompt,
        7,
        channel="chat",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is not None
    assert captured["capability_profile_id"] == "coding"
    assert "workspace:write" in captured["permissions"]
    assert Path(captured["workspace"]).resolve() == (desktop / "todojarvis").resolve()
    assert (desktop / "todojarvis").is_dir()


@pytest.mark.asyncio
async def test_create_task_does_not_start_an_agentic_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api import agentic_processing

    captured = _capture_service(monkeypatch)
    response = await agentic_processing.maybe_start_agentic_run(
        "Crée une tâche pour envoyer le dossier demain",
        42,
        channel="chat",
        voice_mode=False,
        persist_assistant=False,
    )

    assert response is None
    assert captured == {}
