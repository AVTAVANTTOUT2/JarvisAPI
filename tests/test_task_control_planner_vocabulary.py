"""Vocabulaire fermé du planificateur — outils/permissions inventés écartés."""

from __future__ import annotations

from jarvis.task_control.models import ControlTask, new_id
from jarvis.task_control.planner import (
    KNOWN_PERMISSIONS,
    KNOWN_TOOLS,
    _filter_vocabulary,
    build_plan,
)


def test_filter_vocabulary_keeps_known_deduped_and_stable():
    kept = _filter_vocabulary(
        ["read_file", "read_file", " web_search ", "rm_rf", 12, None, ""],
        KNOWN_TOOLS,
    )
    assert kept == ("read_file", "web_search")


def test_filter_vocabulary_rejects_non_list_and_unknown_permissions():
    assert _filter_vocabulary("mail:send", KNOWN_PERMISSIONS) == ()
    assert _filter_vocabulary(None, KNOWN_PERMISSIONS) == ()
    assert (
        _filter_vocabulary(
            ["shell:unrestricted", "deployment:execute", "mail:send"],
            KNOWN_PERMISSIONS,
        )
        == ("mail:send",)
    )


def test_build_plan_strips_invented_tools_and_permissions():
    task = ControlTask(
        task_id=new_id("task"),
        profile_id="default",
        title="Rédiger un mail",
        description="Préparer un brouillon, sans envoyer.",
    )
    plan = build_plan(
        task,
        {
            "objective": "Préparer un brouillon",
            "summary": "Lecture puis brouillon",
            "tools_expected": ["mail_draft", "rm_rf", "shell:unrestricted"],
            "permissions_expected": [
                "mail:send",
                "deployment:execute",
                "shell:unrestricted",
            ],
            "steps": [
                {
                    "title": "Brouillon",
                    "detail": "Rédiger",
                    "tools": ["mail_draft", "invented_tool"],
                    "permissions": ["mail:send", "root:all"],
                }
            ],
            "expected_deliverables": ["Brouillon"],
            "success_criteria": ["Brouillon prêt"],
        },
        version=1,
    )
    assert "rm_rf" not in plan.tools_expected
    assert "invented_tool" not in plan.tools_expected
    assert "mail_draft" in plan.tools_expected
    assert "shell:unrestricted" not in plan.permissions_expected
    assert "deployment:execute" not in plan.permissions_expected
    assert "root:all" not in plan.permissions_expected
    assert "mail:send" in plan.permissions_expected
    assert any("mail:send" in risk for risk in plan.risks)
    assert plan.steps[0].tools == ("mail_draft",)
    assert plan.steps[0].permissions == ("mail:send",)


def test_build_plan_unions_step_vocabulary_into_plan_level():
    task = ControlTask(
        task_id=new_id("task"),
        profile_id="default",
        title="Chercher puis écrire",
        description="Recherche puis note",
    )
    plan = build_plan(
        task,
        {
            "objective": "Chercher et noter",
            "tools_expected": [],
            "permissions_expected": None,
            "steps": [
                {
                    "title": "Chercher",
                    "tools": ["web_search"],
                    "permissions": ["research:search"],
                },
                {
                    "title": "Écrire",
                    "tools": ["write_file"],
                    "permissions": ["workspace:write"],
                },
            ],
        },
        version=2,
    )
    assert plan.tools_expected == ("web_search", "write_file")
    assert plan.permissions_expected == ("research:search", "workspace:write")
