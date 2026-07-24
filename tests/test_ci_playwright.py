"""Contrats structurels de la couverture Playwright du frontend canonique."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
E2E_DIR = ROOT / "frontend" / "e2e"


def _unified_frontend_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  unified_frontend:")
    end = workflow.index("\n  android:", start)
    return workflow[start:end]


def test_ci_runs_playwright_against_the_canonical_frontend():
    job = _unified_frontend_job()

    assert "pnpm exec playwright install --with-deps chrome" in job
    assert "pnpm test && pnpm typecheck && pnpm build && pnpm test:e2e" in job


def test_playwright_covers_the_nine_critical_browser_scenarios():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(E2E_DIR.glob("*.spec.ts"))
    )

    assert source.count("test('@") == 9
    for contract in (
        "unlocks through the real auth form",
        "chat turn over WebSocket",
        "creates and updates a task",
        "consumes an SSE event",
        "selects the responsive dashboard",
        "never reveals private content",
        "loads MapLibre workers",
    ):
        assert contract in source

    for health_check in (
        "page.on('console'",
        "page.on('pageerror'",
        "page.on('requestfailed'",
        "response.status() >= 400",
    ):
        assert health_check in source
