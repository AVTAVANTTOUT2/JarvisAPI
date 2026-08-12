"""Livraison externe explicite des changements validés par JARVIS.

Ce module ne connaît aucun runtime agentique. Le transport est injecté par la
couche JARVIS authentifiée et n'expose volontairement aucune opération de
merge ou de déploiement.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import inspect
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, cast
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from jarvis.agentic.redaction import redact_text

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_MAX_COMMAND_OUTPUT = 64 * 1024
_MAX_COMMAND_INPUT = 24 * 1024
_MAX_API_RESPONSE = 2 * 1024 * 1024
_GIT_TIMEOUT = 120.0
_GH_TIMEOUT = 60.0
_GIT_ASKPASS_SCRIPT = """#!/usr/bin/python3
import os
import sys

prompt = sys.argv[1] if len(sys.argv) == 2 else ""
host = os.environ.get("JARVIS_GIT_ASKPASS_HOST", "")
fd_text = os.environ.get("JARVIS_GIT_TOKEN_FD", "")
if (
    not host
    or host.casefold() not in prompt.casefold()
    or "password" not in prompt.casefold()
    or not fd_text.isascii()
    or not fd_text.isdigit()
):
    raise SystemExit(1)
fd = int(fd_text)
with os.fdopen(fd, "rb", closefd=False) as stream:
    token = stream.read(8193)
if (
    not token
    or len(token) > 8192
    or token != token.strip()
    or any(value < 0x20 for value in token)
):
    raise SystemExit(1)
os.write(1, token + b"\\n")
"""
_SUCCESSFUL_CHECK_STATES = {"completed", "passed", "success", "successful"}
_TRUSTED_EXECUTABLE_DIRECTORIES = tuple(
    Path(value)
    for value in (
        "/usr/bin",
        "/bin",
        "/usr/local/bin",
        "/opt/local/bin",
        "/snap/bin",
    )
)


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _GitHubApiResult:
    status_code: int
    payload: Any


@dataclass(frozen=True)
class EngineeringRemoteIdentity:
    """Identité GitHub figée par JARVIS avant de déléguer le worktree."""

    push_url: str
    gh_repository: str
    host: str
    owner: str
    repository: str

    def to_dict(self) -> dict[str, str]:
        return {
            "push_url": self.push_url,
            "gh_repository": self.gh_repository,
            "host": self.host,
            "owner": self.owner,
            "repository": self.repository,
        }


_CommandRunner = Callable[..., _CommandResult]
_GitHubApiRunner = Callable[..., _GitHubApiResult]


class EngineeringDeliveryTransport(Protocol):
    """Transport JARVIS minimal: push, draft PR idempotente et checks CI."""

    async def push_branch(
        self,
        *,
        workspace: Path,
        branch: str,
        expected_head: str,
        force: bool,
        idempotency_key: str,
        remote_identity: Mapping[str, str] | None,
    ) -> Mapping[str, Any]: ...

    async def ensure_draft_pr(
        self,
        *,
        workspace: Path,
        head_branch: str,
        base_branch: str,
        expected_head: str,
        title: str,
        body: str,
        draft: bool,
        idempotency_key: str,
        remote_identity: Mapping[str, str] | None,
    ) -> Mapping[str, Any]: ...

    async def wait_for_checks(
        self,
        *,
        workspace: Path,
        pr_id: str | None,
        pr_url: str | None,
        expected_head: str,
        head_branch: str,
        base_branch: str,
        required_checks: Sequence[str],
        remote_identity: Mapping[str, str] | None,
        timeout: float,
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...


def _is_writable(path: Path) -> bool:
    try:
        return os.access(path, os.W_OK, effective_ids=True)
    except TypeError:  # pragma: no cover - plateformes sans effective_ids
        return os.access(path, os.W_OK)


def _secure_directory(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    return not any(_is_writable(item) for item in (resolved, *resolved.parents))


def _secure_system_path(path: Path, *, forbidden_root: Path | None = None) -> bool:
    """Refuse les binaires remplaçables par l'utilisateur courant ou le worktree."""

    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        return False
    if info.st_mode & 0o6000 or _is_writable(resolved):
        return False
    if not _secure_directory(path.parent):
        return False
    if forbidden_root is not None:
        root = forbidden_root.resolve(strict=True)
        if resolved == root or root in resolved.parents:
            return False
    for directory in (resolved.parent, *resolved.parents):
        if _is_writable(directory):
            return False
    return True


def _trusted_delivery_path() -> str:
    directories = [
        str(directory.resolve(strict=True))
        for directory in _TRUSTED_EXECUTABLE_DIRECTORIES
        if _secure_directory(directory)
    ]
    # Le contrôle détaillé est refait sur chaque exécutable. PATH sert seulement
    # aux éventuels sous-processus internes de git.
    if not directories:
        directories = ["/usr/bin", "/bin"]
    return os.pathsep.join(dict.fromkeys(directories))


def _delivery_environment() -> dict[str, str]:
    """Construit un environnement minimal, non interactif et de taille bornée."""

    selected: dict[str, str] = {}
    for key, limit in (
        ("LANG", 128),
        ("LC_ALL", 128),
        ("LC_CTYPE", 128),
    ):
        value = os.environ.get(key)
        if value and "\x00" not in value and len(value) <= limit:
            selected[key] = value

    selected["PATH"] = _trusted_delivery_path()
    selected.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "PAGER": "",
            "NO_COLOR": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_ALLOW_PROTOCOL": "https",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "GIT_CONFIG_COUNT": "4",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.devnull,
            "GIT_CONFIG_KEY_1": "commit.gpgSign",
            "GIT_CONFIG_VALUE_1": "false",
            "GIT_CONFIG_KEY_2": "tag.gpgSign",
            "GIT_CONFIG_VALUE_2": "false",
            "GIT_CONFIG_KEY_3": "push.gpgSign",
            "GIT_CONFIG_VALUE_3": "false",
        }
    )
    return selected


def _resolve_executable(name: str, *, forbidden_root: Path | None = None) -> str:
    if name != "git":
        raise RuntimeError("delivery_executable_invalid")
    for directory in _TRUSTED_EXECUTABLE_DIRECTORIES:
        candidate = directory / name
        if _secure_system_path(candidate, forbidden_root=forbidden_root):
            return str(candidate.resolve(strict=True))
    raise RuntimeError(f"delivery_{name}_unavailable")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - exercé sur les runners Windows
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _run_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout: float,
    stdin: str | None = None,
    environment_overlay: Mapping[str, str] | None = None,
    inherited_fds: tuple[int, ...] = (),
) -> _CommandResult:
    """Exécute sans shell en drainant les sorties au-delà de la limite mémoire."""

    if not argv or not Path(argv[0]).is_absolute() or not 0.1 <= timeout <= 300:
        raise RuntimeError("delivery_command_invalid")
    stdin_bytes = (stdin or "").encode("utf-8")
    if len(stdin_bytes) > _MAX_COMMAND_INPUT:
        raise RuntimeError("delivery_command_input_too_large")
    resolved_cwd = cwd.resolve(strict=True)
    executable = Path(argv[0])
    if executable.name == "git" and not _secure_system_path(
        executable, forbidden_root=resolved_cwd
    ):
        raise RuntimeError("delivery_executable_untrusted")
    environment = _delivery_environment()
    allowed_overlay = {
        "GIT_ASKPASS",
        "GIT_ASKPASS_REQUIRE",
        "JARVIS_GIT_ASKPASS_HOST",
        "JARVIS_GIT_TOKEN_FD",
    }
    for key, value in (environment_overlay or {}).items():
        if (
            key not in allowed_overlay
            or not isinstance(value, str)
            or not value
            or len(value) > 4_096
            or "\x00" in value
        ):
            raise RuntimeError("delivery_command_environment_invalid")
        environment[key] = value
    if inherited_fds and os.name == "nt":
        raise RuntimeError("delivery_secure_git_transport_unsupported")
    if any(not isinstance(fd, int) or fd < 0 for fd in inherited_fds):
        raise RuntimeError("delivery_command_fd_invalid")
    popen_kwargs: dict[str, Any] = {
        "cwd": str(resolved_cwd),
        # Aucun bearer n'entre dans argv/env/fichier. Le seul subprocessus
        # authentifié le reçoit via un FD one-shot explicitement hérité.
        "env": environment,
        "stdin": subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
        popen_kwargs["pass_fds"] = inherited_fds
    try:
        process = cast(subprocess.Popen[bytes], subprocess.Popen(argv, **popen_kwargs))
    except OSError as exc:
        raise RuntimeError("delivery_command_start_failed") from exc

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()

    def drain(stream: Any, target: bytearray) -> None:
        while True:
            chunk = stream.read(8_192)
            if not chunk:
                return
            remaining = _MAX_COMMAND_OUTPUT - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])

    assert process.stdout is not None
    assert process.stderr is not None
    readers = (
        threading.Thread(
            target=drain, args=(process.stdout, stdout_buffer), daemon=True
        ),
        threading.Thread(
            target=drain, args=(process.stderr, stderr_buffer), daemon=True
        ),
    )
    for reader in readers:
        reader.start()
    writer: threading.Thread | None = None
    if process.stdin is not None:

        def write_stdin() -> None:
            assert process.stdin is not None
            try:
                process.stdin.write(stdin_bytes)
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

        writer = threading.Thread(target=write_stdin, daemon=True)
        writer.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        process.wait()
        raise RuntimeError("delivery_command_timeout") from exc
    finally:
        if writer is not None:
            writer.join(timeout=2)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
    return _CommandResult(
        returncode=returncode,
        stdout=stdout_buffer.decode("utf-8", errors="replace"),
        stderr=stderr_buffer.decode("utf-8", errors="replace"),
    )


def _write_private_file(path: Path, payload: bytes, *, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _read_git_pointer(path: Path, *, label: str) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"delivery_{label}_invalid") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4_096:
        raise RuntimeError(f"delivery_{label}_invalid")
    content = path.read_text(encoding="utf-8")
    if "\x00" in content or "\n" in content.rstrip("\n"):
        raise RuntimeError(f"delivery_{label}_invalid")
    return content.strip()


def _repository_object_directory(workspace: Path) -> Path:
    """Résout l'object store sans charger de configuration Git contrôlable."""

    root = workspace.resolve(strict=True)
    marker = root / ".git"
    try:
        marker_info = marker.lstat()
    except OSError as exc:
        raise RuntimeError("delivery_git_metadata_invalid") from exc
    if stat.S_ISDIR(marker_info.st_mode):
        git_dir = marker
    elif stat.S_ISREG(marker_info.st_mode):
        pointer = _read_git_pointer(marker, label="git_metadata")
        if not pointer.startswith("gitdir: "):
            raise RuntimeError("delivery_git_metadata_invalid")
        raw_git_dir = Path(pointer[8:])
        git_dir = raw_git_dir if raw_git_dir.is_absolute() else root / raw_git_dir
    else:
        raise RuntimeError("delivery_git_metadata_invalid")
    git_dir = git_dir.resolve(strict=True)
    common_pointer = git_dir / "commondir"
    if common_pointer.exists():
        raw_common = Path(_read_git_pointer(common_pointer, label="git_commondir"))
        common_dir = raw_common if raw_common.is_absolute() else git_dir / raw_common
        common_dir = common_dir.resolve(strict=True)
    else:
        common_dir = git_dir
    objects = common_dir / "objects"
    try:
        objects_info = objects.lstat()
        common_info = common_dir.lstat()
    except OSError as exc:
        raise RuntimeError("delivery_git_objects_invalid") from exc
    if not stat.S_ISDIR(common_info.st_mode) or not stat.S_ISDIR(objects_info.st_mode):
        raise RuntimeError("delivery_git_objects_invalid")
    if os.name != "nt":
        uid = os.getuid()
        if common_info.st_uid != uid or objects_info.st_uid != uid:
            raise RuntimeError("delivery_git_objects_owner_invalid")
        if common_info.st_mode & 0o022 or objects_info.st_mode & 0o022:
            raise RuntimeError("delivery_git_objects_mode_invalid")
    return objects.resolve(strict=True)


@dataclass(frozen=True)
class _IsolatedGitRepository:
    root: Path
    git_dir: Path
    askpass: Path


@contextmanager
def _isolated_git_repository(
    workspace: Path,
    *,
    branch: str,
    expected_head: str,
) -> Iterator[_IsolatedGitRepository]:
    """Crée une vue bare sans config locale/globale, adossée aux objets vérifiés."""

    if os.name == "nt":
        raise RuntimeError("delivery_secure_git_transport_unsupported")
    python = Path("/usr/bin/python3")
    if not _secure_system_path(python):
        raise RuntimeError("delivery_askpass_runtime_unavailable")
    objects = _repository_object_directory(workspace)
    object_text = str(objects)
    if not object_text or "\n" in object_text or "\x00" in object_text:
        raise RuntimeError("delivery_git_objects_invalid")
    with tempfile.TemporaryDirectory(
        prefix="jarvis-git-delivery-", dir="/tmp"
    ) as raw_root:
        root = Path(raw_root).resolve(strict=True)
        os.chmod(root, 0o700)
        root_info = root.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_mode & 0o077:
            raise RuntimeError("delivery_git_sandbox_invalid")
        git_dir = root / "repo.git"
        (git_dir / "objects" / "info").mkdir(parents=True, mode=0o700)
        ref = git_dir / "refs" / "heads" / branch
        ref.parent.mkdir(parents=True, mode=0o700)
        config = (
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = true\n"
            "\tbare = true\n"
            "\tlogallrefupdates = false\n"
            "[credential]\n"
            "\thelper =\n"
            "[http]\n"
            "\tfollowRedirects = false\n"
            "\tsslVerify = true\n"
            "[protocol]\n"
            "\tallow = never\n"
            '[protocol "https"]\n'
            "\tallow = always\n"
        ).encode("utf-8")
        _write_private_file(git_dir / "config", config, mode=0o600)
        _write_private_file(
            git_dir / "HEAD", f"ref: refs/heads/{branch}\n".encode(), mode=0o600
        )
        _write_private_file(ref, f"{expected_head}\n".encode(), mode=0o600)
        _write_private_file(
            git_dir / "objects" / "info" / "alternates",
            f"{object_text}\n".encode(),
            mode=0o600,
        )
        askpass = root / "askpass"
        _write_private_file(askpass, _GIT_ASKPASS_SCRIPT.encode("utf-8"), mode=0o500)
        yield _IsolatedGitRepository(root=root, git_dir=git_dir, askpass=askpass)


def _safe_sha(value: object) -> str:
    sha = str(value or "").strip().casefold()
    if not _SHA_RE.fullmatch(sha):
        raise ValueError("delivery_head_invalid")
    return sha


def _safe_idempotency_key(value: object) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 512 or any(char in key for char in "\r\n\x00"):
        raise ValueError("delivery_idempotency_key_invalid")
    return key


def _safe_error(exc: BaseException) -> str:
    value = str(exc)
    if re.fullmatch(r"delivery_[a-z0-9_]+", value):
        return value
    return "delivery_transport_error"


def _remote_identity_from_url(raw_url: object) -> EngineeringRemoteIdentity:
    parsed = urlsplit(str(raw_url or "").strip())
    host = (parsed.hostname or "").casefold()
    configured_host = os.environ.get("GH_HOST", "").strip().casefold()
    allowed_hosts = {"github.com"}
    if re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", configured_host
    ):
        allowed_hosts.add(configured_host)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or host not in allowed_hosts
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
    ):
        raise RuntimeError("delivery_remote_invalid")
    owner = parts[0]
    repository = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    if not _REPOSITORY_PART_RE.fullmatch(owner) or not _REPOSITORY_PART_RE.fullmatch(
        repository
    ):
        raise RuntimeError("delivery_remote_invalid")
    gh_repository = f"{owner}/{repository}"
    if host != "github.com":
        gh_repository = f"{host}/{gh_repository}"
    return EngineeringRemoteIdentity(
        push_url=f"https://{host}/{owner}/{repository}.git",
        gh_repository=gh_repository,
        host=host,
        owner=owner,
        repository=repository,
    )


def _coerce_remote_identity(
    value: EngineeringRemoteIdentity | Mapping[str, Any] | None,
) -> EngineeringRemoteIdentity:
    if isinstance(value, EngineeringRemoteIdentity):
        candidate = value
    elif isinstance(value, Mapping) and set(value) == {
        "push_url",
        "gh_repository",
        "host",
        "owner",
        "repository",
    }:
        candidate = EngineeringRemoteIdentity(
            push_url=str(value["push_url"]),
            gh_repository=str(value["gh_repository"]),
            host=str(value["host"]),
            owner=str(value["owner"]),
            repository=str(value["repository"]),
        )
    else:
        raise RuntimeError("delivery_remote_identity_missing")
    canonical = _remote_identity_from_url(candidate.push_url)
    if candidate != canonical:
        raise RuntimeError("delivery_remote_identity_invalid")
    return canonical


def normalize_remote_identity(
    value: EngineeringRemoteIdentity | Mapping[str, Any] | None,
) -> EngineeringRemoteIdentity:
    return _coerce_remote_identity(value)


def normalize_required_checks(values: Sequence[str]) -> tuple[str, ...]:
    checks: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or len(value) > 200 or any(ord(char) < 0x20 for char in value):
            raise ValueError("delivery_required_check_invalid")
        if value not in checks:
            checks.append(value)
    return tuple(checks)


def capture_engineering_remote_identity(
    workspace: Path,
) -> EngineeringRemoteIdentity | None:
    """Capture l'origin avant délégation avec un git système non remplaçable."""

    resolved_workspace = workspace.resolve(strict=True)
    git = _resolve_executable("git", forbidden_root=resolved_workspace)
    result = _run_command(
        (git, "remote", "get-url", "--push", "origin"),
        cwd=resolved_workspace,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return _remote_identity_from_url(result.stdout.strip())


def _github_token(explicit: str | None = None) -> str:
    token = str(
        explicit or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    )
    if (
        not token
        or len(token) > 8_192
        or token != token.strip()
        or any(ord(char) < 0x20 for char in token)
    ):
        raise RuntimeError("delivery_github_token_unavailable")
    return token


def _github_api_base(identity: EngineeringRemoteIdentity) -> str:
    if identity.host == "github.com":
        return "https://api.github.com"
    return f"https://{identity.host}/api/v3"


def _run_github_api(
    identity: EngineeringRemoteIdentity,
    method: str,
    path: str,
    *,
    params: Mapping[str, str | int] | None,
    json_body: Mapping[str, Any] | None,
    timeout: float,
    token: str,
    transport: httpx.BaseTransport | None = None,
) -> _GitHubApiResult:
    expected_prefix = f"/repos/{identity.owner}/{identity.repository}/"
    if (
        method not in {"GET", "POST", "PATCH"}
        or not path.startswith(expected_prefix)
        or "//" in path
        or "?" in path
        or "#" in path
        or not 0.1 <= timeout <= _GH_TIMEOUT
    ):
        raise RuntimeError("delivery_github_request_invalid")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {_github_token(token)}",
        "User-Agent": "jarvis-devagent-delivery/1",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    deadline = monotonic() + timeout
    try:
        with httpx.Client(
            headers=headers,
            follow_redirects=False,
            # Un flux qui produit un petit chunk juste avant chaque timeout de
            # lecture ne peut pas repousser indéfiniment la deadline globale.
            timeout=httpx.Timeout(timeout, read=min(5.0, timeout)),
            transport=transport,
            trust_env=False,
        ) as client:
            with client.stream(
                method,
                f"{_github_api_base(identity)}{path}",
                params=dict(params or {}),
                json=dict(json_body) if json_body is not None else None,
            ) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() >= deadline:
                        raise RuntimeError("delivery_github_request_timeout")
                    if len(content) + len(chunk) > _MAX_API_RESPONSE:
                        raise RuntimeError("delivery_github_response_too_large")
                    content.extend(chunk)
                if monotonic() >= deadline:
                    raise RuntimeError("delivery_github_request_timeout")
                if not content:
                    payload: Any = None
                else:
                    try:
                        payload = json.loads(bytes(content))
                    except (UnicodeError, ValueError):
                        payload = None
                return _GitHubApiResult(response.status_code, payload)
    except RuntimeError:
        raise
    except httpx.HTTPError:
        # Le bearer reste uniquement dans les headers de l'objet local; aucune
        # exception httpx (qui conserve la requête) ne traverse cette frontière.
        raise RuntimeError("delivery_github_api_unavailable") from None


class ProductionEngineeringDeliveryTransport:
    """Transport GitHub borné: push exact, draft PR et observation CI seulement."""

    def __init__(
        self,
        *,
        command_runner: _CommandRunner | None = None,
        api_runner: _GitHubApiRunner | None = None,
        github_token: str | None = None,
        http_transport: httpx.BaseTransport | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        if not 0.01 <= poll_interval <= 60:
            raise ValueError("delivery_check_interval_invalid")
        self._runner = command_runner or _run_command
        self._api_runner = api_runner
        self._github_token = _github_token(github_token) if github_token else None
        self._http_transport = http_transport
        self._poll_interval = poll_interval
        self._git_executable = (
            _resolve_executable("git") if command_runner is None else "git"
        )

    def _run(
        self,
        executable: str,
        args: tuple[str, ...],
        *,
        workspace: Path,
        timeout: float,
        stdin: str | None = None,
        environment_overlay: Mapping[str, str] | None = None,
        inherited_fds: tuple[int, ...] = (),
    ) -> _CommandResult:
        result = self._runner(
            (executable, *args),
            cwd=workspace,
            timeout=timeout,
            stdin=stdin,
            environment_overlay=environment_overlay,
            inherited_fds=inherited_fds,
        )
        if not isinstance(result, _CommandResult):
            raise RuntimeError("delivery_command_result_invalid")
        return result

    def _git(
        self, workspace: Path, *args: str, timeout: float = _GIT_TIMEOUT
    ) -> _CommandResult:
        return self._run(
            self._git_executable, tuple(args), workspace=workspace, timeout=timeout
        )

    def _isolated_git(
        self,
        repository: _IsolatedGitRepository,
        *args: str,
        timeout: float = _GIT_TIMEOUT,
        authenticated_host: str | None = None,
    ) -> _CommandResult:
        command = (
            f"--git-dir={repository.git_dir}",
            "-c",
            "credential.helper=",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.sslVerify=true",
            *args,
        )
        if authenticated_host is None:
            return self._run(
                self._git_executable,
                command,
                workspace=repository.root,
                timeout=timeout,
            )
        token = _github_token(self._github_token).encode("utf-8")
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(write_fd, "wb", closefd=False) as stream:
                stream.write(token)
                stream.flush()
        finally:
            os.close(write_fd)
        try:
            return self._run(
                self._git_executable,
                command,
                workspace=repository.root,
                timeout=timeout,
                environment_overlay={
                    "GIT_ASKPASS": str(repository.askpass),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "JARVIS_GIT_ASKPASS_HOST": authenticated_host,
                    "JARVIS_GIT_TOKEN_FD": str(read_fd),
                },
                inherited_fds=(read_fd,),
            )
        finally:
            os.close(read_fd)

    @staticmethod
    def _require_success(result: _CommandResult, code: str) -> str:
        if result.returncode != 0:
            raise RuntimeError(code)
        return result.stdout.strip()

    def _api(
        self,
        identity: EngineeringRemoteIdentity,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout: float = _GH_TIMEOUT,
    ) -> _GitHubApiResult:
        if self._api_runner is not None:
            result = self._api_runner(
                identity,
                method,
                path,
                params=params,
                json_body=json_body,
                timeout=timeout,
            )
        else:
            result = _run_github_api(
                identity,
                method,
                path,
                params=params,
                json_body=json_body,
                timeout=timeout,
                token=_github_token(self._github_token),
                transport=self._http_transport,
            )
        if not isinstance(result, _GitHubApiResult):
            raise RuntimeError("delivery_github_result_invalid")
        return result

    def _api_payload(
        self,
        identity: EngineeringRemoteIdentity,
        method: str,
        path: str,
        *,
        expected_status: int,
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, Any] | None = None,
        timeout: float = _GH_TIMEOUT,
        error: str,
    ) -> Any:
        result = self._api(
            identity,
            method,
            path,
            params=params,
            json_body=json_body,
            timeout=timeout,
        )
        if result.status_code != expected_status:
            raise RuntimeError(error)
        return result.payload

    def _remote_identity(
        self, workspace: Path, *, timeout: float = 30.0
    ) -> EngineeringRemoteIdentity:
        raw_url = self._require_success(
            self._git(
                workspace,
                "remote",
                "get-url",
                "--push",
                "origin",
                timeout=timeout,
            ),
            "delivery_remote_unavailable",
        )
        return _remote_identity_from_url(raw_url)

    def _verified_remote_identity(
        self,
        workspace: Path,
        expected: EngineeringRemoteIdentity | Mapping[str, Any] | None,
        *,
        timeout: float = 30.0,
    ) -> EngineeringRemoteIdentity:
        captured = _coerce_remote_identity(expected)
        current = self._remote_identity(workspace, timeout=timeout)
        if current != captured:
            raise RuntimeError("delivery_remote_identity_changed")
        return captured

    def _list_prs(
        self,
        workspace: Path,
        identity: EngineeringRemoteIdentity,
        *,
        head_branch: str,
        base_branch: str,
    ) -> list[Mapping[str, Any]]:
        del workspace
        raw = self._api_payload(
            identity,
            "GET",
            f"/repos/{identity.owner}/{identity.repository}/pulls",
            expected_status=200,
            params={
                "state": "open",
                "head": f"{identity.owner}:{head_branch}",
                "base": base_branch,
                "per_page": 2,
            },
            error="delivery_pr_lookup_failed",
        )
        if not isinstance(raw, list) or any(
            not isinstance(item, Mapping) for item in raw
        ):
            raise RuntimeError("delivery_pr_lookup_invalid")
        return raw

    def _view_pr(
        self,
        workspace: Path,
        identity: EngineeringRemoteIdentity,
        selector: str,
        *,
        timeout: float = _GH_TIMEOUT,
    ) -> Mapping[str, Any]:
        del workspace
        if not selector.isdigit() or int(selector) <= 0:
            raise RuntimeError("delivery_pr_identity_invalid")
        value = self._api_payload(
            identity,
            "GET",
            f"/repos/{identity.owner}/{identity.repository}/pulls/{selector}",
            expected_status=200,
            timeout=timeout,
            error="delivery_pr_view_failed",
        )
        if not isinstance(value, Mapping):
            raise RuntimeError("delivery_pr_view_invalid")
        return value

    @staticmethod
    def _validate_pr(
        value: Mapping[str, Any],
        identity: EngineeringRemoteIdentity,
        *,
        head_branch: str,
        base_branch: str,
        expected_head: str,
    ) -> tuple[str, str]:
        number = value.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise RuntimeError("delivery_pr_identity_invalid")
        url = _safe_pr_url(value.get("html_url"))
        parsed = urlsplit(url)
        expected_path = f"/{identity.owner}/{identity.repository}/pull/{number}"
        if (
            parsed.hostname != identity.host
            or parsed.path.casefold() != expected_path.casefold()
        ):
            raise RuntimeError("delivery_pr_identity_invalid")
        if value.get("draft") is not True:
            raise RuntimeError("delivery_existing_pr_not_draft")
        head = value.get("head")
        base = value.get("base")
        if not isinstance(head, Mapping) or not isinstance(base, Mapping):
            raise RuntimeError("delivery_pr_identity_invalid")
        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if not isinstance(head_repo, Mapping) or not isinstance(base_repo, Mapping):
            raise RuntimeError("delivery_pr_identity_invalid")
        expected_repo = f"{identity.owner}/{identity.repository}".casefold()
        if str(head_repo.get("full_name") or "").casefold() != expected_repo:
            raise RuntimeError("delivery_cross_repository_pr_forbidden")
        if str(base_repo.get("full_name") or "").casefold() != expected_repo:
            raise RuntimeError("delivery_pr_identity_invalid")
        if str(value.get("state") or "").casefold() != "open":
            raise RuntimeError("delivery_pr_not_open")
        if str(head.get("ref") or "") != head_branch:
            raise RuntimeError("delivery_pr_head_branch_mismatch")
        if str(base.get("ref") or "") != base_branch:
            raise RuntimeError("delivery_pr_base_branch_mismatch")
        if _safe_sha(head.get("sha")) != expected_head:
            raise RuntimeError("delivery_pr_head_mismatch")
        return str(number), url

    def _push_branch_sync(
        self,
        *,
        workspace: Path,
        branch: str,
        expected_head: str,
        force: bool,
        idempotency_key: str,
        remote_identity: Mapping[str, str] | None,
    ) -> Mapping[str, Any]:
        if force:
            raise RuntimeError("delivery_force_push_forbidden")
        branch = _safe_branch(branch)
        expected_head = _safe_sha(expected_head)
        _safe_idempotency_key(idempotency_key)
        actual_head = _safe_sha(
            self._require_success(
                self._git(workspace, "rev-parse", "--verify", "HEAD^{commit}"),
                "delivery_head_unavailable",
            )
        )
        if actual_head != expected_head:
            raise RuntimeError("delivery_push_head_mismatch")
        actual_branch = self._require_success(
            self._git(workspace, "branch", "--show-current"),
            "delivery_branch_unavailable",
        )
        if actual_branch != branch:
            raise RuntimeError("delivery_branch_mismatch")
        identity = self._verified_remote_identity(workspace, remote_identity)
        authenticated_url = urlunsplit(
            (
                "https",
                f"x-access-token@{identity.host}",
                f"/{identity.owner}/{identity.repository}.git",
                "",
                "",
            )
        )
        with _isolated_git_repository(
            workspace,
            branch=branch,
            expected_head=expected_head,
        ) as repository:
            available = self._isolated_git(
                repository,
                "cat-file",
                "-e",
                f"{expected_head}^{{commit}}",
                timeout=30,
            )
            self._require_success(available, "delivery_head_unavailable")
            pushed = self._isolated_git(
                repository,
                "push",
                "--porcelain",
                "--no-force",
                "--no-verify",
                "--no-signed",
                authenticated_url,
                f"{expected_head}:refs/heads/{branch}",
                authenticated_host=identity.host,
            )
            self._require_success(pushed, "delivery_push_rejected")
            remote = self._require_success(
                self._isolated_git(
                    repository,
                    "ls-remote",
                    "--exit-code",
                    "--heads",
                    authenticated_url,
                    f"refs/heads/{branch}",
                    authenticated_host=identity.host,
                ),
                "delivery_remote_head_unavailable",
            )
        fields = remote.split()
        if len(fields) != 2 or _safe_sha(fields[0]) != expected_head:
            raise RuntimeError("delivery_remote_head_mismatch")
        if fields[1] != f"refs/heads/{branch}":
            raise RuntimeError("delivery_remote_ref_mismatch")
        return {"ok": True, "head_sha": expected_head}

    async def push_branch(self, **kwargs: Any) -> Mapping[str, Any]:
        try:
            return await asyncio.to_thread(self._push_branch_sync, **kwargs)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "error": _safe_error(exc)}

    def _ensure_draft_pr_sync(
        self,
        *,
        workspace: Path,
        head_branch: str,
        base_branch: str,
        expected_head: str,
        title: str,
        body: str,
        draft: bool,
        idempotency_key: str,
        remote_identity: Mapping[str, str] | None,
    ) -> Mapping[str, Any]:
        if not draft:
            raise RuntimeError("delivery_ready_pr_forbidden")
        head_branch = _safe_branch(head_branch)
        base_branch = _safe_branch(base_branch)
        expected_head = _safe_sha(expected_head)
        _safe_idempotency_key(idempotency_key)
        if (
            not title.strip()
            or len(title) > 240
            or len(body.encode("utf-8")) > _MAX_COMMAND_INPUT
        ):
            raise RuntimeError("delivery_pr_content_invalid")
        identity = self._verified_remote_identity(workspace, remote_identity)
        candidates = self._list_prs(
            workspace,
            identity,
            head_branch=head_branch,
            base_branch=base_branch,
        )
        if len(candidates) > 1:
            raise RuntimeError("delivery_pr_ambiguous")
        if candidates:
            number, _url = self._validate_pr(
                candidates[0],
                identity,
                head_branch=head_branch,
                base_branch=base_branch,
                expected_head=expected_head,
            )
            final = self._api_payload(
                identity,
                "PATCH",
                f"/repos/{identity.owner}/{identity.repository}/pulls/{number}",
                expected_status=200,
                json_body={"title": title, "body": body},
                error="delivery_pr_update_failed",
            )
        else:
            created = self._api(
                identity,
                "POST",
                f"/repos/{identity.owner}/{identity.repository}/pulls",
                json_body={
                    "title": title,
                    "body": body,
                    "head": f"{identity.owner}:{head_branch}",
                    "base": base_branch,
                    "draft": True,
                },
            )
            if created.status_code == 201:
                final = created.payload
            else:
                if created.status_code != 422:
                    raise RuntimeError("delivery_pr_create_failed")
                candidates = self._list_prs(
                    workspace,
                    identity,
                    head_branch=head_branch,
                    base_branch=base_branch,
                )
                if len(candidates) != 1:
                    raise RuntimeError("delivery_pr_create_failed")
                number, _url = self._validate_pr(
                    candidates[0],
                    identity,
                    head_branch=head_branch,
                    base_branch=base_branch,
                    expected_head=expected_head,
                )
                final = self._api_payload(
                    identity,
                    "PATCH",
                    f"/repos/{identity.owner}/{identity.repository}/pulls/{number}",
                    expected_status=200,
                    json_body={"title": title, "body": body},
                    error="delivery_pr_update_failed",
                )
        if not isinstance(final, Mapping):
            raise RuntimeError("delivery_pr_view_invalid")
        number, url = self._validate_pr(
            final,
            identity,
            head_branch=head_branch,
            base_branch=base_branch,
            expected_head=expected_head,
        )
        return {"ok": True, "draft": True, "pr_id": number, "url": url}

    async def ensure_draft_pr(self, **kwargs: Any) -> Mapping[str, Any]:
        try:
            return await asyncio.to_thread(self._ensure_draft_pr_sync, **kwargs)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "error": _safe_error(exc)}

    def _check_runs(
        self,
        identity: EngineeringRemoteIdentity,
        expected_head: str,
        *,
        deadline: float,
    ) -> list[Mapping[str, Any]]:
        values: list[Mapping[str, Any]] = []
        for page in range(1, 11):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise RuntimeError("delivery_checks_timeout")
            payload = self._api_payload(
                identity,
                "GET",
                f"/repos/{identity.owner}/{identity.repository}/commits/"
                f"{quote(expected_head, safe='')}/check-runs",
                expected_status=200,
                params={"per_page": 100, "page": page, "filter": "latest"},
                timeout=min(_GH_TIMEOUT, remaining),
                error="delivery_checks_query_failed",
            )
            if monotonic() >= deadline:
                raise RuntimeError("delivery_checks_timeout")
            if not isinstance(payload, Mapping):
                raise RuntimeError("delivery_checks_invalid")
            total = payload.get("total_count")
            page_values = payload.get("check_runs")
            if (
                not isinstance(total, int)
                or isinstance(total, bool)
                or total < 0
                or total > 1_000
                or not isinstance(page_values, list)
                or any(not isinstance(item, Mapping) for item in page_values)
            ):
                raise RuntimeError("delivery_checks_invalid")
            values.extend(page_values)
            if len(values) >= total:
                return values
            if len(page_values) != 100:
                raise RuntimeError("delivery_checks_invalid")
        raise RuntimeError("delivery_checks_too_many")

    def _commit_statuses(
        self,
        identity: EngineeringRemoteIdentity,
        expected_head: str,
        *,
        deadline: float,
    ) -> list[Mapping[str, Any]]:
        values: list[Mapping[str, Any]] = []
        for page in range(1, 11):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise RuntimeError("delivery_checks_timeout")
            payload = self._api_payload(
                identity,
                "GET",
                f"/repos/{identity.owner}/{identity.repository}/commits/"
                f"{quote(expected_head, safe='')}/statuses",
                expected_status=200,
                params={"per_page": 100, "page": page},
                timeout=min(_GH_TIMEOUT, remaining),
                error="delivery_checks_query_failed",
            )
            if monotonic() >= deadline:
                raise RuntimeError("delivery_checks_timeout")
            if not isinstance(payload, list) or any(
                not isinstance(item, Mapping) for item in payload
            ):
                raise RuntimeError("delivery_checks_invalid")
            values.extend(payload)
            if len(payload) < 100:
                return values
        raise RuntimeError("delivery_checks_too_many")

    def _check_buckets(
        self,
        identity: EngineeringRemoteIdentity,
        expected_head: str,
        *,
        deadline: float,
    ) -> dict[str, list[str]]:
        observed: dict[str, list[str]] = {}
        for item in self._check_runs(identity, expected_head, deadline=deadline):
            name = str(item.get("name") or "").strip()
            status = str(item.get("status") or "").casefold()
            conclusion = str(item.get("conclusion") or "").casefold()
            if not name or len(name) > 200:
                raise RuntimeError("delivery_checks_invalid")
            if status in {"queued", "in_progress", "pending", "requested", "waiting"}:
                bucket = "pending"
            elif status != "completed":
                bucket = "unknown"
            elif conclusion == "success":
                bucket = "pass"
            elif conclusion in {"cancelled"}:
                bucket = "cancel"
            elif conclusion in {"neutral", "skipped"}:
                bucket = "skipping"
            elif conclusion in {
                "action_required",
                "failure",
                "stale",
                "startup_failure",
                "timed_out",
            }:
                bucket = "fail"
            else:
                bucket = "unknown"
            observed.setdefault(name, []).append(bucket)

        # L'API renvoie les statuts du plus récent au plus ancien. Un contexte
        # n'est conservé qu'une fois afin qu'un ancien échec ne masque pas son
        # rerun courant.
        seen_statuses: set[str] = set()
        for item in self._commit_statuses(identity, expected_head, deadline=deadline):
            name = str(item.get("context") or "").strip()
            state = str(item.get("state") or "").casefold()
            if not name or len(name) > 200:
                raise RuntimeError("delivery_checks_invalid")
            if name in seen_statuses:
                continue
            seen_statuses.add(name)
            if state == "success":
                bucket = "pass"
            elif state == "pending":
                bucket = "pending"
            elif state in {"failure", "error"}:
                bucket = "fail"
            else:
                bucket = "unknown"
            observed.setdefault(name, []).append(bucket)
        return observed

    async def wait_for_checks(
        self,
        *,
        workspace: Path,
        pr_id: str | None,
        pr_url: str | None,
        expected_head: str,
        head_branch: str,
        base_branch: str,
        required_checks: Sequence[str],
        remote_identity: Mapping[str, str] | None,
        timeout: float,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        try:
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not 1 <= timeout <= 7_200
            ):
                raise RuntimeError("delivery_checks_timeout_invalid")
            expected_head = _safe_sha(expected_head)
            head_branch = _safe_branch(head_branch)
            base_branch = _safe_branch(base_branch)
            required = normalize_required_checks(required_checks)
            if not required:
                raise RuntimeError("delivery_required_checks_missing")
            _safe_idempotency_key(idempotency_key)
            deadline = monotonic() + timeout
            remaining = deadline - monotonic()
            identity = await asyncio.to_thread(
                self._verified_remote_identity,
                workspace,
                remote_identity,
                timeout=min(30.0, max(0.1, remaining)),
            )
            selector = str(pr_id or "").strip()
            if selector and not selector.isdigit():
                raise RuntimeError("delivery_pr_identity_invalid")
            if not selector:
                selector = _pr_number_from_url(pr_url, identity)
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return {
                        "ok": False,
                        "status": "timeout",
                        "error": "checks_timeout",
                    }
                current = await asyncio.to_thread(
                    self._view_pr,
                    workspace,
                    identity,
                    selector,
                    timeout=min(_GH_TIMEOUT, max(0.1, remaining)),
                )
                _number, _url = self._validate_pr(
                    current,
                    identity,
                    head_branch=head_branch,
                    base_branch=base_branch,
                    expected_head=expected_head,
                )
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return {"ok": False, "status": "timeout", "error": "checks_timeout"}
                try:
                    observed = await asyncio.to_thread(
                        self._check_buckets,
                        identity,
                        expected_head,
                        deadline=deadline,
                    )
                except RuntimeError as exc:
                    if str(exc) == "delivery_checks_timeout":
                        return {
                            "ok": False,
                            "status": "timeout",
                            "error": "checks_timeout",
                        }
                    raise
                buckets = {bucket for states in observed.values() for bucket in states}
                if buckets & {"fail", "cancel"} or buckets - {
                    "pass",
                    "pending",
                    "skipping",
                }:
                    return {"ok": False, "status": "failure", "error": "checks_failed"}
                missing = [name for name in required if name not in observed]
                required_passed = not missing and all(
                    states and all(state == "pass" for state in observed[name])
                    for name, states in ((name, observed[name]) for name in required)
                )
                if required_passed and "pending" not in buckets:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return {
                            "ok": False,
                            "status": "timeout",
                            "error": "checks_timeout",
                        }
                    final = await asyncio.to_thread(
                        self._view_pr,
                        workspace,
                        identity,
                        selector,
                        timeout=min(
                            _GH_TIMEOUT,
                            max(0.1, remaining),
                        ),
                    )
                    if monotonic() >= deadline:
                        return {
                            "ok": False,
                            "status": "timeout",
                            "error": "checks_timeout",
                        }
                    self._validate_pr(
                        final,
                        identity,
                        head_branch=head_branch,
                        base_branch=base_branch,
                        expected_head=expected_head,
                    )
                    final_identity = await asyncio.to_thread(
                        self._verified_remote_identity,
                        workspace,
                        identity,
                        timeout=min(_GIT_TIMEOUT, max(0.1, deadline - monotonic())),
                    )
                    if final_identity != identity:
                        raise RuntimeError("delivery_remote_identity_changed")
                    if monotonic() >= deadline:
                        return {
                            "ok": False,
                            "status": "timeout",
                            "error": "checks_timeout",
                        }
                    return {
                        "ok": True,
                        "status": "passed",
                        "checks": sum(len(states) for states in observed.values()),
                        "required_checks": list(required),
                    }
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return {"ok": False, "status": "timeout", "error": "checks_timeout"}
                await asyncio.sleep(min(self._poll_interval, remaining))
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "status": "failure", "error": _safe_error(exc)}


def _safe_branch(branch: str) -> str:
    value = str(branch).strip()
    if (
        not _BRANCH_RE.fullmatch(value)
        or ".." in value
        or "@{" in value
        or value.endswith(("/", ".", ".lock"))
    ):
        raise ValueError("delivery_branch_invalid")
    return value


def _git_value(workspace: Path, *args: str) -> str:
    result = _run_command(
        (
            _resolve_executable("git"),
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            *args,
        ),
        cwd=workspace,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("delivery_git_state_unavailable")
    return result.stdout.strip()


async def _invoke_transport(
    transport: EngineeringDeliveryTransport, method_name: str, **kwargs: Any
) -> Mapping[str, Any]:
    method = getattr(transport, method_name, None)
    if not callable(method):
        raise RuntimeError(f"delivery_transport_missing_{method_name}")
    result = method(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise RuntimeError(f"delivery_transport_invalid_{method_name}")
    return result


def _failure(status: str, detail: object = "") -> dict[str, Any]:
    return {
        "ok": False,
        "performed": True,
        "status": status,
        "detail": redact_text(str(detail), max_chars=500),
    }


def _safe_pr_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("delivery_pr_url_invalid")
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("https", host, parsed.path, "", ""))


def _pr_number_from_url(value: object, identity: EngineeringRemoteIdentity) -> str:
    url = _safe_pr_url(value)
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.hostname != identity.host
        or len(parts) != 4
        or parts[0].casefold() != identity.owner.casefold()
        or parts[1].casefold() != identity.repository.casefold()
        or parts[2] != "pull"
        or not parts[3].isdigit()
        or int(parts[3]) <= 0
    ):
        raise RuntimeError("delivery_pr_identity_invalid")
    return parts[3]


async def deliver_engineering_change(
    worktree: Any,
    *,
    title: str,
    body: str,
    transport: EngineeringDeliveryTransport | None,
    enabled: bool = False,
    idempotency_key: str | None = None,
    checks_timeout: float = 900.0,
) -> dict[str, Any]:
    """Pousse, upsert une draft PR puis attend la CI, uniquement sur opt-in."""

    if not enabled:
        return {"ok": True, "performed": False, "status": "external_delivery_disabled"}
    if (
        isinstance(checks_timeout, bool)
        or not isinstance(checks_timeout, (int, float))
        or not 1 <= checks_timeout <= 7_200
    ):
        return _failure("delivery_checks_timeout_invalid")
    if transport is None:
        try:
            transport = ProductionEngineeringDeliveryTransport()
        except (OSError, RuntimeError, ValueError) as exc:
            return _failure("delivery_transport_unavailable", _safe_error(exc))

    try:
        workspace = Path(worktree.workspace).resolve(strict=True)
        branch = _safe_branch(worktree.branch)
        base_branch = _safe_branch(worktree.base_branch)
        raw_remote_identity = getattr(worktree, "remote_identity", None)
        remote_identity = (
            _coerce_remote_identity(raw_remote_identity)
            if raw_remote_identity is not None
            else None
        )
        required_checks = normalize_required_checks(
            tuple(getattr(worktree, "required_checks", ()))
        )
        if remote_identity is None:
            raise RuntimeError("delivery_remote_identity_missing")
        if not required_checks:
            raise RuntimeError("delivery_required_checks_missing")
        if _git_value(workspace, "status", "--porcelain=v1", "--untracked-files=all"):
            return _failure("delivery_worktree_not_clean")
        if _git_value(workspace, "branch", "--show-current") != branch:
            return _failure("delivery_branch_mismatch")
        head_sha = _git_value(workspace, "rev-parse", "HEAD")
        if not _SHA_RE.fullmatch(head_sha):
            return _failure("delivery_head_invalid")
    except (OSError, RuntimeError, ValueError) as exc:
        return _failure("delivery_preflight_failed", exc)

    stable_key = (
        idempotency_key
        or hashlib.sha256(
            f"{Path(worktree.repo_root).resolve()}\0{branch}\0{head_sha}".encode()
        ).hexdigest()
    )
    try:
        _safe_idempotency_key(stable_key)
    except ValueError as exc:
        return _failure("delivery_idempotency_key_invalid", exc)
    safe_title = redact_text(" ".join(str(title).splitlines()), max_chars=240)
    safe_body = redact_text(str(body), max_chars=20_000)
    try:
        pushed = await _invoke_transport(
            transport,
            "push_branch",
            workspace=workspace,
            branch=branch,
            expected_head=head_sha,
            force=False,
            idempotency_key=stable_key,
            remote_identity=(
                remote_identity.to_dict() if remote_identity is not None else None
            ),
        )
        if pushed.get("ok") is not True:
            return _failure("delivery_push_failed", pushed.get("error"))
        pushed_head = str(pushed.get("head_sha") or head_sha)
        if pushed_head != head_sha:
            return _failure("delivery_push_head_mismatch")

        pull_request = await _invoke_transport(
            transport,
            "ensure_draft_pr",
            workspace=workspace,
            head_branch=branch,
            base_branch=base_branch,
            expected_head=head_sha,
            title=safe_title,
            body=safe_body,
            draft=True,
            idempotency_key=stable_key,
            remote_identity=(
                remote_identity.to_dict() if remote_identity is not None else None
            ),
        )
        if pull_request.get("ok") is not True:
            return _failure("delivery_pr_failed", pull_request.get("error"))
        if pull_request.get("draft") is not True:
            return _failure("delivery_pr_not_draft")
        pr_id = str(pull_request.get("pr_id") or "").strip()
        if pr_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", pr_id):
            return _failure("delivery_pr_identity_invalid")
        pr_url = _safe_pr_url(pull_request.get("url"))
        if not pr_id and not pr_url:
            return _failure("delivery_pr_identity_missing")

        checks = await _invoke_transport(
            transport,
            "wait_for_checks",
            workspace=workspace,
            pr_id=pr_id or None,
            pr_url=pr_url or None,
            expected_head=head_sha,
            head_branch=branch,
            base_branch=base_branch,
            required_checks=required_checks,
            remote_identity=(
                remote_identity.to_dict() if remote_identity is not None else None
            ),
            timeout=checks_timeout,
            idempotency_key=stable_key,
        )
        check_status = str(checks.get("status") or "").strip().casefold()
        if checks.get("ok") is not True or check_status not in _SUCCESSFUL_CHECK_STATES:
            return _failure(
                "delivery_checks_failed", check_status or checks.get("error")
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return _failure("delivery_transport_failed", exc)

    return {
        "ok": True,
        "performed": True,
        "status": "checks_passed",
        "delivery_key": stable_key,
        "head_sha": head_sha,
        "pr": {"pr_id": pr_id or None, "url": pr_url or None, "draft": True},
        "checks": {"status": check_status},
    }


__all__ = [
    "EngineeringDeliveryTransport",
    "EngineeringRemoteIdentity",
    "ProductionEngineeringDeliveryTransport",
    "capture_engineering_remote_identity",
    "deliver_engineering_change",
    "normalize_required_checks",
    "normalize_remote_identity",
]
