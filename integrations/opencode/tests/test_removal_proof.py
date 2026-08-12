from integrations.opencode.tools.removal_proof import (
    REMOVAL_PROOF_STEPS,
    REPOSITORY_ROOT,
    run_removal_proof,
)


def test_plugin_removal_keeps_generic_jarvis_contract() -> None:
    result = run_removal_proof(REPOSITORY_ROOT)

    assert result["plugin_copy_removed"] is True
    assert result["source_worktree_untouched"] is True
    assert result["provider_discovered"] is False
    assert result["provider_status"] == "provider_unavailable"
    assert result["production_references"] == []
    assert result["network_events"] == []
    assert result["spawn_events"] == []
    assert result["api_paths"] == 2
    assert result["compiled_python_files"] > 0
    assert result["database_tables"] >= 8
    assert result["full_delivery_gates"] is False
    assert [step["name"] for step in result["steps"]] == list(REMOVAL_PROOF_STEPS)
    assert [step["id"] for step in result["steps"]] == list(range(1, 17))
    assert all(step["status"] == "passed" for step in result["steps"][:8])
    assert all(
        step["status"] == "delegated_to_delivery_gates"
        for step in result["steps"][8:15]
    )
    assert result["steps"][15]["status"] == "passed"
