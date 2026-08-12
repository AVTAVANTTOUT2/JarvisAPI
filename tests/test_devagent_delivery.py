from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from agents.devagent.agentic_runtime import prepare_engineering_worktree
import agents.devagent.delivery as delivery_module
from agents.devagent.delivery import (
    ProductionEngineeringDeliveryTransport,
    deliver_engineering_change,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _committed_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "jarvis@example.invalid")
    _git(repo, "config", "user.name", "JARVIS Tests")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/acme/project.git")
    worktree = prepare_engineering_worktree(
        repo_root=repo,
        job_id="delivery-job",
        required_checks=("ci",),
    )
    (worktree.workspace / "README.md").write_text("delivered\n", encoding="utf-8")
    _git(worktree.workspace, "add", "README.md")
    _git(worktree.workspace, "commit", "-m", "validated")
    return worktree


class _Transport:
    def __init__(self, *, checks_ok: bool = True) -> None:
        self.checks_ok = checks_ok
        self.calls: list[tuple[str, dict]] = []
        self.merge_calls = 0
        self.deploy_calls = 0

    async def push_branch(self, **kwargs):
        self.calls.append(("push", kwargs))
        return {"ok": True, "head_sha": kwargs["expected_head"]}

    async def ensure_draft_pr(self, **kwargs):
        self.calls.append(("pr", kwargs))
        return {
            "ok": True,
            "draft": True,
            "pr_id": "pr-17",
            "url": "https://example.invalid/pr/17",
        }

    async def wait_for_checks(self, **kwargs):
        self.calls.append(("checks", kwargs))
        return {
            "ok": self.checks_ok,
            "status": "success" if self.checks_ok else "failure",
        }

    async def merge(self, **_kwargs):  # pragma: no cover - doit rester inaccessible
        self.merge_calls += 1
        raise AssertionError("merge interdit")

    async def deploy(self, **_kwargs):  # pragma: no cover - doit rester inaccessible
        self.deploy_calls += 1
        raise AssertionError("déploiement interdit")


@pytest.mark.asyncio
async def test_external_delivery_is_disabled_by_default(tmp_path):
    worktree = _committed_worktree(tmp_path)
    transport = _Transport()

    result = await deliver_engineering_change(
        worktree,
        title="Draft",
        body="Body",
        transport=transport,
    )

    assert result == {
        "ok": True,
        "performed": False,
        "status": "external_delivery_disabled",
    }
    assert transport.calls == []


@pytest.mark.asyncio
async def test_enabled_delivery_uses_production_transport_when_not_injected(
    tmp_path, monkeypatch
):
    worktree = _committed_worktree(tmp_path)
    transport = _Transport()
    monkeypatch.setattr(
        delivery_module,
        "ProductionEngineeringDeliveryTransport",
        lambda: transport,
    )

    result = await deliver_engineering_change(
        worktree,
        title="Draft",
        body="Body",
        transport=None,
        enabled=True,
    )

    assert result["status"] == "checks_passed"
    assert [name for name, _kwargs in transport.calls] == ["push", "pr", "checks"]


@pytest.mark.asyncio
async def test_delivery_pushes_upserts_draft_and_waits_for_ci_idempotently(tmp_path):
    worktree = _committed_worktree(tmp_path)
    transport = _Transport()

    first = await deliver_engineering_change(
        worktree,
        title="Validated change",
        body="Tests passed",
        transport=transport,
        enabled=True,
        idempotency_key="delivery:stable",
        checks_timeout=120,
    )
    second = await deliver_engineering_change(
        worktree,
        title="Validated change",
        body="Tests passed",
        transport=transport,
        enabled=True,
        idempotency_key="delivery:stable",
        checks_timeout=120,
    )

    assert first["status"] == second["status"] == "checks_passed"
    assert first["delivery_key"] == second["delivery_key"] == "delivery:stable"
    assert [name for name, _kwargs in transport.calls] == [
        "push",
        "pr",
        "checks",
        "push",
        "pr",
        "checks",
    ]
    pr_calls = [kwargs for name, kwargs in transport.calls if name == "pr"]
    assert all(call["draft"] is True for call in pr_calls)
    assert all(call["idempotency_key"] == "delivery:stable" for call in pr_calls)
    assert all(
        call["force"] is False for name, call in transport.calls if name == "push"
    )
    assert transport.merge_calls == 0
    assert transport.deploy_calls == 0


@pytest.mark.asyncio
async def test_failed_checks_never_merge_or_deploy(tmp_path):
    worktree = _committed_worktree(tmp_path)
    transport = _Transport(checks_ok=False)

    result = await deliver_engineering_change(
        worktree,
        title="Validated change",
        body="Tests passed",
        transport=transport,
        enabled=True,
    )

    assert result["ok"] is False
    assert result["status"] == "delivery_checks_failed"
    assert transport.merge_calls == 0
    assert transport.deploy_calls == 0


@pytest.mark.asyncio
async def test_transport_cannot_downgrade_the_pr_to_ready(tmp_path):
    worktree = _committed_worktree(tmp_path)
    transport = _Transport()

    async def ready_pr(**kwargs):
        transport.calls.append(("pr", kwargs))
        return {"ok": True, "draft": False, "pr_id": "pr-17"}

    transport.ensure_draft_pr = ready_pr
    result = await deliver_engineering_change(
        worktree,
        title="Validated change",
        body="Tests passed",
        transport=transport,
        enabled=True,
    )

    assert result["status"] == "delivery_pr_not_draft"
    assert [name for name, _kwargs in transport.calls] == ["push", "pr"]
    assert transport.merge_calls == 0
    assert transport.deploy_calls == 0


@pytest.mark.asyncio
async def test_production_transport_pushes_exact_head_and_upserts_one_draft(tmp_path):
    worktree = _committed_worktree(tmp_path)
    malicious_marker = tmp_path / "credential-helper-ran"
    _git(
        worktree.workspace,
        "config",
        "credential.helper",
        f"!touch {malicious_marker}",
    )
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    calls: list[tuple[tuple[str, ...], float, str | None]] = []
    api_calls: list[tuple[str, str, dict, dict | None]] = []
    inherited_tokens: list[bytes] = []
    created = False

    def pr_payload():
        return {
            "number": 17,
            "html_url": "https://github.com/acme/project/pull/17",
            "draft": True,
            "state": "open",
            "head": {
                "ref": worktree.branch,
                "sha": head,
                "repo": {"full_name": "acme/project"},
            },
            "base": {
                "ref": worktree.base_branch,
                "repo": {"full_name": "acme/project"},
            },
            "title": "Validated change",
            "body": "Tests passed",
        }

    def runner(
        argv,
        *,
        cwd,
        timeout,
        stdin=None,
        environment_overlay=None,
        inherited_fds=(),
    ):
        nonlocal created
        del cwd
        command = tuple(argv)
        calls.append((command, timeout, stdin))
        executable = Path(command[0]).name
        args = command[1:]
        if executable == "git":
            if args[:3] == ("rev-parse", "--verify", "HEAD^{commit}"):
                return delivery_module._CommandResult(0, f"{head}\n", "")
            if args == ("branch", "--show-current"):
                return delivery_module._CommandResult(0, f"{worktree.branch}\n", "")
            if args == ("remote", "get-url", "--push", "origin"):
                return delivery_module._CommandResult(
                    0, "https://github.com/acme/project.git\n", ""
                )
            if "cat-file" in args:
                git_dir_arg = next(
                    value for value in args if value.startswith("--git-dir=")
                )
                isolated_config = Path(git_dir_arg.split("=", 1)[1]) / "config"
                assert isolated_config.is_file()
                assert "credential-helper-ran" not in isolated_config.read_text(
                    encoding="utf-8"
                )
                return delivery_module._CommandResult(0, "", "")
            if "push" in args:
                assert environment_overlay is not None
                assert environment_overlay["GIT_ASKPASS_REQUIRE"] == "force"
                assert len(inherited_fds) == 1
                inherited_tokens.append(os.read(inherited_fds[0], 100))
                assert "test-token" not in "\n".join(command)
                assert "test-token" not in repr(environment_overlay)
                return delivery_module._CommandResult(0, "ok\n", "")
            if "ls-remote" in args:
                assert environment_overlay is not None
                assert len(inherited_fds) == 1
                inherited_tokens.append(os.read(inherited_fds[0], 100))
                return delivery_module._CommandResult(
                    0, f"{head}\trefs/heads/{worktree.branch}\n", ""
                )
        raise AssertionError(command)

    def api_runner(_identity, method, path, *, params, json_body, timeout):
        nonlocal created
        del timeout
        api_calls.append((method, path, dict(params or {}), json_body))
        if method == "GET" and path.endswith("/pulls"):
            return delivery_module._GitHubApiResult(
                200, [pr_payload()] if created else []
            )
        if method == "POST" and path.endswith("/pulls"):
            created = True
            return delivery_module._GitHubApiResult(201, pr_payload())
        if method == "PATCH" and path.endswith("/pulls/17"):
            return delivery_module._GitHubApiResult(200, pr_payload())
        if method == "GET" and path.endswith("/pulls/17"):
            return delivery_module._GitHubApiResult(200, pr_payload())
        if method == "GET" and path.endswith("/check-runs"):
            return delivery_module._GitHubApiResult(
                200,
                {
                    "total_count": 1,
                    "check_runs": [
                        {"name": "ci", "status": "completed", "conclusion": "success"}
                    ],
                },
            )
        if method == "GET" and path.endswith("/statuses"):
            return delivery_module._GitHubApiResult(200, [])
        raise AssertionError((method, path, params, json_body))

    transport = ProductionEngineeringDeliveryTransport(
        command_runner=runner,
        api_runner=api_runner,
        github_token="test-token",
    )
    for _attempt in range(2):
        result = await deliver_engineering_change(
            worktree,
            title="Validated change",
            body="Tests passed",
            transport=transport,
            enabled=True,
            idempotency_key="delivery:production-stable",
            checks_timeout=30,
        )
        assert result["status"] == "checks_passed"

    commands = [command for command, _timeout, _stdin in calls]
    pushes = [command for command in commands if "push" in command[1:]]
    assert len(pushes) == 2
    assert all(
        "--no-force" in command
        and "--no-verify" in command
        and "--no-signed" in command
        for command in pushes
    )
    assert all(
        "--force" not in command and "--force-with-lease" not in command
        for command in pushes
    )
    assert all(f"{head}:refs/heads/{worktree.branch}" in command for command in pushes)
    assert (
        sum(
            method == "POST" and path.endswith("/pulls")
            for method, path, _, _ in api_calls
        )
        == 1
    )
    assert sum(method == "PATCH" for method, _path, _, _ in api_calls) == 1
    assert not any("merge" in command or "deploy" in command for command in commands)
    pr_writes = [
        body
        for method, _path, _params, body in api_calls
        if method in {"POST", "PATCH"}
    ]
    assert all(body and body["body"] == "Tests passed" for body in pr_writes)
    assert all(0 < timeout <= 120 for _command, timeout, _stdin in calls)
    assert inherited_tokens == [b"test-token", b"test-token"] * 2
    assert not malicious_marker.exists()


@pytest.mark.asyncio
async def test_production_transport_refuses_head_drift_before_push(tmp_path):
    worktree = _committed_worktree(tmp_path)
    expected_head = "a" * 40
    calls: list[tuple[str, ...]] = []

    def runner(argv, **_kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:4] == ("rev-parse", "--verify", "HEAD^{commit}"):
            return delivery_module._CommandResult(0, f"{'b' * 40}\n", "")
        raise AssertionError(command)

    result = await ProductionEngineeringDeliveryTransport(
        command_runner=runner
    ).push_branch(
        workspace=worktree.workspace,
        branch=worktree.branch,
        expected_head=expected_head,
        force=False,
        idempotency_key="delivery:head-drift",
        remote_identity=worktree.remote_identity.to_dict(),
    )

    assert result == {"ok": False, "error": "delivery_push_head_mismatch"}
    assert not any(command[1:2] == ("push",) for command in calls)


@pytest.mark.asyncio
async def test_production_transport_refuses_origin_poisoning_before_push(tmp_path):
    worktree = _committed_worktree(tmp_path)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=worktree.workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    calls: list[tuple[str, ...]] = []

    def runner(argv, **_kwargs):
        command = tuple(argv)
        calls.append(command)
        if command[1:4] == ("rev-parse", "--verify", "HEAD^{commit}"):
            return delivery_module._CommandResult(0, f"{head}\n", "")
        if command[1:] == ("branch", "--show-current"):
            return delivery_module._CommandResult(0, f"{worktree.branch}\n", "")
        if command[1:] == ("remote", "get-url", "--push", "origin"):
            return delivery_module._CommandResult(
                0, "https://github.com/attacker/exfiltration.git\n", ""
            )
        raise AssertionError(command)

    assert worktree.remote_identity is not None
    result = await ProductionEngineeringDeliveryTransport(
        command_runner=runner
    ).push_branch(
        workspace=worktree.workspace,
        branch=worktree.branch,
        expected_head=head,
        force=False,
        idempotency_key="delivery:origin-poisoning",
        remote_identity=worktree.remote_identity.to_dict(),
    )

    assert result == {"ok": False, "error": "delivery_remote_identity_changed"}
    assert not any(command[1:2] == ("push",) for command in calls)


@pytest.mark.asyncio
async def test_ci_waits_for_every_required_check_and_revalidates_target(tmp_path):
    worktree = _committed_worktree(tmp_path)
    head = "a" * 40
    checks_calls = 0
    view_calls = 0

    def pr_payload(*, base_branch: str | None = None):
        return {
            "number": 17,
            "html_url": "https://github.com/acme/project/pull/17",
            "draft": True,
            "state": "open",
            "head": {
                "ref": worktree.branch,
                "sha": head,
                "repo": {"full_name": "acme/project"},
            },
            "base": {
                "ref": base_branch or worktree.base_branch,
                "repo": {"full_name": "acme/project"},
            },
            "title": "Validated change",
            "body": "Tests passed",
        }

    def runner(argv, **_kwargs):
        command = tuple(argv)
        if command[1:] == ("remote", "get-url", "--push", "origin"):
            return delivery_module._CommandResult(
                0, "https://github.com/acme/project.git\n", ""
            )
        raise AssertionError(command)

    def api_runner(_identity, method, path, *, params, json_body, timeout):
        nonlocal checks_calls, view_calls
        del params, json_body, timeout
        if method == "GET" and path.endswith("/pulls/17"):
            view_calls += 1
            base = "release" if view_calls == 3 else worktree.base_branch
            return delivery_module._GitHubApiResult(200, pr_payload(base_branch=base))
        if method == "GET" and path.endswith("/check-runs"):
            checks_calls += 1
            names = ["ci"] if checks_calls == 1 else ["ci", "security"]
            return delivery_module._GitHubApiResult(
                200,
                {
                    "total_count": len(names),
                    "check_runs": [
                        {
                            "name": name,
                            "status": "completed",
                            "conclusion": "success",
                        }
                        for name in names
                    ],
                },
            )
        if method == "GET" and path.endswith("/statuses"):
            return delivery_module._GitHubApiResult(200, [])
        raise AssertionError((method, path))

    assert worktree.remote_identity is not None
    result = await ProductionEngineeringDeliveryTransport(
        command_runner=runner, api_runner=api_runner, poll_interval=0.01
    ).wait_for_checks(
        workspace=worktree.workspace,
        pr_id="17",
        pr_url=None,
        expected_head=head,
        head_branch=worktree.branch,
        base_branch=worktree.base_branch,
        required_checks=("ci", "security"),
        remote_identity=worktree.remote_identity.to_dict(),
        timeout=2,
        idempotency_key="delivery:ci-policy",
    )

    assert checks_calls == 2
    assert result == {
        "ok": False,
        "status": "failure",
        "error": "delivery_pr_base_branch_mismatch",
    }


@pytest.mark.asyncio
async def test_ci_pagination_shares_one_global_deadline(tmp_path, monkeypatch):
    worktree = _committed_worktree(tmp_path)
    head = "a" * 40
    clock = 0.0
    page_timeouts: list[float] = []

    def fake_monotonic() -> float:
        return clock

    monkeypatch.setattr(delivery_module, "monotonic", fake_monotonic)

    def pr_payload():
        return {
            "number": 17,
            "html_url": "https://github.com/acme/project/pull/17",
            "draft": True,
            "state": "open",
            "head": {
                "ref": worktree.branch,
                "sha": head,
                "repo": {"full_name": "acme/project"},
            },
            "base": {
                "ref": worktree.base_branch,
                "repo": {"full_name": "acme/project"},
            },
        }

    def runner(argv, **_kwargs):
        command = tuple(argv)
        if command[1:] == ("remote", "get-url", "--push", "origin"):
            return delivery_module._CommandResult(
                0, "https://github.com/acme/project.git\n", ""
            )
        raise AssertionError(command)

    def api_runner(_identity, method, path, *, params, json_body, timeout):
        nonlocal clock
        del params, json_body
        if method == "GET" and path.endswith("/pulls/17"):
            return delivery_module._GitHubApiResult(200, pr_payload())
        if method == "GET" and path.endswith("/check-runs"):
            page_timeouts.append(timeout)
            clock += 0.6
            return delivery_module._GitHubApiResult(
                200,
                {
                    "total_count": 1_000,
                    "check_runs": [
                        {
                            "name": f"optional-{index}",
                            "status": "completed",
                            "conclusion": "success",
                        }
                        for index in range(100)
                    ],
                },
            )
        raise AssertionError((method, path))

    assert worktree.remote_identity is not None
    result = await ProductionEngineeringDeliveryTransport(
        command_runner=runner, api_runner=api_runner, poll_interval=0.01
    ).wait_for_checks(
        workspace=worktree.workspace,
        pr_id="17",
        pr_url=None,
        expected_head=head,
        head_branch=worktree.branch,
        base_branch=worktree.base_branch,
        required_checks=("required",),
        remote_identity=worktree.remote_identity.to_dict(),
        timeout=1,
        idempotency_key="delivery:global-deadline",
    )

    assert result == {
        "ok": False,
        "status": "timeout",
        "error": "checks_timeout",
    }
    assert len(page_timeouts) == 2
    assert page_timeouts[0] == pytest.approx(1.0)
    assert page_timeouts[1] == pytest.approx(0.4)


def test_delivery_ignores_path_hijack_and_never_gives_git_the_bearer(
    tmp_path, monkeypatch
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("GH_TOKEN", "ghp_attack_test_token")

    resolved = Path(delivery_module._resolve_executable("git", forbidden_root=tmp_path))
    git_environment = delivery_module._delivery_environment()

    assert resolved != fake_git
    assert tmp_path not in resolved.parents
    assert str(fake_bin) not in git_environment["PATH"].split(os.pathsep)
    assert "GH_TOKEN" not in git_environment


def test_github_rest_keeps_bearer_in_parent_and_uses_versioned_strict_url():
    identity = delivery_module.normalize_remote_identity(
        {
            "push_url": "https://github.com/acme/project.git",
            "gh_repository": "acme/project",
            "host": "github.com",
            "owner": "acme",
            "repository": "project",
        }
    )
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers["Authorization"]
        observed["version"] = request.headers["X-GitHub-Api-Version"]
        return httpx.Response(200, json=[])

    result = delivery_module._run_github_api(
        identity,
        "GET",
        "/repos/acme/project/pulls",
        params={"state": "open"},
        json_body=None,
        timeout=10,
        token="github-test-token",
        transport=httpx.MockTransport(handler),
    )

    assert result == delivery_module._GitHubApiResult(200, [])
    assert observed == {
        "url": "https://api.github.com/repos/acme/project/pulls?state=open",
        "authorization": "Bearer github-test-token",
        "version": "2026-03-10",
    }


def test_github_rest_stops_oversized_stream_without_leaking_token(monkeypatch):
    identity = delivery_module.normalize_remote_identity(
        {
            "push_url": "https://github.com/acme/project.git",
            "gh_repository": "acme/project",
            "host": "github.com",
            "owner": "acme",
            "repository": "project",
        }
    )
    token = "github-stream-secret"

    class GuardedStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0

        def __iter__(self):
            for chunk in (b"123456", b"abcdef", b"must-not-be-read"):
                self.yielded += 1
                if self.yielded > 2:
                    raise AssertionError("le lecteur a continué après dépassement")
                yield chunk

    stream = GuardedStream()
    monkeypatch.setattr(delivery_module, "_MAX_API_RESPONSE", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {token}"
        return httpx.Response(200, stream=stream)

    with pytest.raises(
        RuntimeError, match="delivery_github_response_too_large"
    ) as error:
        delivery_module._run_github_api(
            identity,
            "GET",
            "/repos/acme/project/pulls",
            params=None,
            json_body=None,
            timeout=10,
            token=token,
            transport=httpx.MockTransport(handler),
        )

    assert stream.yielded == 2
    assert token not in str(error.value)
    assert error.value.__cause__ is None


def test_github_rest_slow_stream_obeys_one_request_deadline(monkeypatch):
    identity = delivery_module.normalize_remote_identity(
        {
            "push_url": "https://github.com/acme/project.git",
            "gh_repository": "acme/project",
            "host": "github.com",
            "owner": "acme",
            "repository": "project",
        }
    )
    clock = 0.0

    def fake_monotonic() -> float:
        return clock

    class SlowStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.yielded = 0

        def __iter__(self):
            nonlocal clock
            for _index in range(4):
                self.yielded += 1
                clock += 0.6
                yield b"{}"

    stream = SlowStream()
    monkeypatch.setattr(delivery_module, "monotonic", fake_monotonic)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with pytest.raises(RuntimeError, match="delivery_github_request_timeout"):
        delivery_module._run_github_api(
            identity,
            "GET",
            "/repos/acme/project/pulls",
            params=None,
            json_body=None,
            timeout=1,
            token="test-token",
            transport=httpx.MockTransport(handler),
        )

    assert stream.yielded == 2


def test_git_askpass_reads_fake_token_only_from_inherited_fd(tmp_path):
    askpass = tmp_path / "askpass"
    delivery_module._write_private_file(
        askpass,
        delivery_module._GIT_ASKPASS_SCRIPT.encode("utf-8"),
        mode=0o500,
    )
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"fake-parent-token")
    os.close(write_fd)
    try:
        result = delivery_module._run_command(
            ("/usr/bin/git", "credential", "fill"),
            cwd=tmp_path,
            timeout=10,
            stdin=("protocol=https\nhost=github.com\nusername=x-access-token\n\n"),
            environment_overlay={
                "GIT_ASKPASS": str(askpass),
                "GIT_ASKPASS_REQUIRE": "force",
                "JARVIS_GIT_ASKPASS_HOST": "github.com",
                "JARVIS_GIT_TOKEN_FD": str(read_fd),
            },
            inherited_fds=(read_fd,),
        )
    finally:
        os.close(read_fd)

    assert result.returncode == 0
    assert "password=fake-parent-token" in result.stdout
    assert "fake-parent-token" not in repr(delivery_module._delivery_environment())


def test_production_transport_never_resolves_homebrew_gh(monkeypatch):
    resolved: list[str] = []

    def fake_resolve(name: str, **_kwargs) -> str:
        resolved.append(name)
        if name != "git":
            raise AssertionError("gh ne doit jamais être résolu")
        return "/usr/bin/git"

    monkeypatch.setenv("PATH", "/opt/homebrew/bin")
    monkeypatch.setattr(delivery_module, "_resolve_executable", fake_resolve)

    transport = ProductionEngineeringDeliveryTransport()

    assert transport._git_executable == "/usr/bin/git"
    assert resolved == ["git"]


def test_delivery_subprocess_environment_and_output_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "99")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/tmp/attacker-hooks")
    monkeypatch.setenv("GIT_SSH_COMMAND", "attacker-command")
    environment = delivery_module._delivery_environment()

    assert environment["GIT_CONFIG_COUNT"] == "4"
    assert environment["GIT_CONFIG_VALUE_0"] == os.devnull
    assert environment["GIT_CONFIG_VALUE_3"] == "false"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["GIT_CONFIG_SYSTEM"] == os.devnull
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONSAFEPATH"] == "1"
    assert "GIT_SSH_COMMAND" not in environment
    assert "HOME" not in environment
    assert "XDG_CONFIG_HOME" not in environment
    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment

    result = delivery_module._run_command(
        (sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"),
        cwd=tmp_path,
        timeout=10,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode("utf-8")) == delivery_module._MAX_COMMAND_OUTPUT
    assert result.stderr == ""


def test_delivery_subprocess_timeout_includes_blocked_stdin(tmp_path):
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="delivery_command_timeout"):
        delivery_module._run_command(
            (sys.executable, "-c", "import time; time.sleep(30)"),
            cwd=tmp_path,
            timeout=1,
            stdin="x" * 20_000,
        )
    assert time.monotonic() - started < 5
