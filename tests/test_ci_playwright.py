"""Contrats structurels de la couverture Playwright du frontend canonique."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
E2E_DIR = ROOT / "frontend" / "e2e"
STATIC_CSP_SERVER = E2E_DIR / "serve-static-csp.py"


def _unified_frontend_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("  unified_frontend:")
    end = workflow.index("\n  android:", start)
    return workflow[start:end]


def test_ci_runs_playwright_against_the_canonical_frontend():
    job = _unified_frontend_job()

    assert "pnpm exec playwright install --with-deps chrome" in job
    assert "pnpm test && pnpm typecheck && pnpm build && pnpm test:e2e" in job


def test_static_csp_server_fails_closed_without_the_next_build():
    server = STATIC_CSP_SERVER.read_text(encoding="utf-8")

    assert 'OUT = ROOT / "frontend" / "out"' in server
    assert "if not OUT.is_dir():" in server
    assert "sys.exit(1)" in server


def test_playwright_covers_the_critical_browser_scenarios():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(E2E_DIR.glob("*.spec.ts"))
    )

    assert source.count("test('@") >= 9
    for contract in (
        "unlocks through the real auth form",
        "keeps an idle lock across reload",
        "chat turn over WebSocket",
        "creates and updates a task",
        "consumes an SSE event",
        "never reveals private content",
        "shows initial PIN setup after static export with security headers",
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
