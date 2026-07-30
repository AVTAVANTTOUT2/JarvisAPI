"""Contrats du garde-fou qualité progressif."""

from pathlib import Path


def test_quality_commands_cover_every_standard_control():
    from scripts.quality_gate import quality_commands

    commands = quality_commands(
        [
            "api/router_example.py",
            "frontend/src/example.tsx",
            "tests/test_example.py",
        ]
    )
    tools = [command[0] for command in commands]

    assert tools == ["ruff", "black", "mypy", "bandit", "semgrep"]
    assert "api/router_example.py" in commands[0]
    assert "tests/test_example.py" in commands[0]
    assert "tests/test_example.py" not in next(
        command for command in commands if command[0] == "bandit"
    )


def test_quality_configuration_declares_a_real_coverage_threshold():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (root / "requirements-quality.txt").read_text(encoding="utf-8")

    assert "fail_under = 35" in pyproject
    for tool in ("ruff", "black", "mypy", "bandit", "pip-audit", "coverage", "semgrep"):
        assert tool in requirements


def test_ci_runs_quality_dependency_and_coverage_gates():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for contract in (
        "quality:",
        "python scripts/quality_gate.py",
        "pip-audit --local",
        "pnpm --dir frontend audit",
        "pnpm --dir web audit",
        "npm --prefix pwa audit",
        "coverage run -m pytest",
        "coverage report",
    ):
        assert contract in workflow
