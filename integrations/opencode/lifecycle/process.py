"""Gestion sûre et idempotente de ``opencode serve``."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time
from typing import Any, Mapping
from uuid import uuid4

from integrations.opencode.config import (
    OpenCodeSettings,
    RuntimeLayout,
    normalize_runtime_config_overlay,
)
from integrations.opencode.security.environment import build_child_environment
from integrations.opencode.security.paths import ensure_within
from integrations.opencode.security.redaction import redact_text

from ._files import atomic_write_json, read_json_object
from .health import HealthReport, check_health
from .install import InstallManager, InstallationError
from .release import ReleaseManifest


class ProcessManagerError(RuntimeError):
    pass


class ProcessOwnershipError(ProcessManagerError):
    pass


_MAX_INHERITED_DESCRIPTORS = 8


@dataclass(frozen=True, slots=True)
class ProcessState:
    pid: int
    port: int
    hostname: str
    binary_path: str
    workspace: str
    instance_id: str
    version: str
    started_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProcessState":
        try:
            state = cls(
                pid=int(value["pid"]),
                port=int(value["port"]),
                hostname=str(value["hostname"]),
                binary_path=str(value["binary_path"]),
                workspace=str(value["workspace"]),
                instance_id=str(value["instance_id"]),
                version=str(value["version"]),
                started_at=str(value["started_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProcessManagerError("État du processus OpenCode invalide") from exc
        if (
            state.pid <= 1
            or not (1 <= state.port <= 65535)
            or state.hostname != "127.0.0.1"
        ):
            raise ProcessManagerError("État du processus OpenCode hors politique")
        return state

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@dataclass(frozen=True, slots=True)
class ProcessStatus:
    running: bool
    healthy: bool
    owned: bool
    pid: int | None
    port: int | None
    version: str | None
    error_code: str | None = None


class OpenCodeProcessManager:
    def __init__(
        self,
        *,
        layout: RuntimeLayout | None = None,
        settings: OpenCodeSettings | None = None,
        manifest: ReleaseManifest | None = None,
        install_manager: InstallManager | None = None,
    ) -> None:
        self.layout = layout or RuntimeLayout.default()
        self.settings = settings or OpenCodeSettings()
        self.manifest = manifest or ReleaseManifest.load()
        self.install_manager = install_manager or InstallManager(
            layout=self.layout,
            settings=self.settings,
            manifest=self.manifest,
        )
        self._process: subprocess.Popen[Any] | None = None

    @staticmethod
    def _descriptor_inheritance_kwargs(
        inherited_fds: tuple[int, ...],
    ) -> dict[str, Any]:
        if len(inherited_fds) > _MAX_INHERITED_DESCRIPTORS:
            raise ProcessManagerError("trop de descripteurs héritables OpenCode")
        normalized: list[int] = []
        for fd in inherited_fds:
            if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
                raise ProcessManagerError("descripteur héritable OpenCode invalide")
            if fd in normalized:
                raise ProcessManagerError("descripteur héritable OpenCode dupliqué")
            try:
                os.fstat(fd)
                inheritable = os.get_inheritable(fd)
            except OSError as exc:
                raise ProcessManagerError(
                    "descripteur héritable OpenCode inaccessible"
                ) from exc
            if not inheritable:
                raise ProcessManagerError("descripteur OpenCode non héritable")
            normalized.append(fd)
        if not normalized:
            return {}
        if os.name != "nt":
            return {"pass_fds": tuple(normalized)}

        try:
            import msvcrt

            startupinfo_factory = getattr(subprocess, "STARTUPINFO")
            get_handle_inheritable = getattr(os, "get_handle_inheritable")
            handles = tuple(msvcrt.get_osfhandle(fd) for fd in normalized)
            if any(
                handle < 0 or not get_handle_inheritable(handle) for handle in handles
            ):
                raise OSError("handle non héritable")
            startupinfo = startupinfo_factory()
            startupinfo.lpAttributeList = {"handle_list": list(handles)}
        except (AttributeError, ImportError, OSError) as exc:
            raise ProcessManagerError(
                "héritage explicite de descripteur indisponible sur Windows"
            ) from exc
        return {"startupinfo": startupinfo}

    def start(
        self,
        *,
        workspace: Path | None = None,
        explicit_environment: Mapping[str, str] | None = None,
        additional_environment_allowlist: tuple[str, ...] = (),
        runtime_config_overlay: Mapping[str, Any] | None = None,
        inherited_fds: tuple[int, ...] = (),
    ) -> ProcessState:
        """Démarre un serveur privé ou retourne l'instance saine existante."""

        self.layout.ensure()
        descriptor_kwargs = self._descriptor_inheritance_kwargs(inherited_fds)
        normalized_overlay = normalize_runtime_config_overlay(runtime_config_overlay)
        existing = self._read_state(optional=True)
        if existing is not None and self._pid_alive(existing.pid):
            current = self.status()
            if current.healthy and current.owned and runtime_config_overlay is None:
                return existing
            if current.owned:
                self.stop()
            else:
                raise ProcessOwnershipError(
                    "Un PID vivant non attribuable bloque le démarrage OpenCode"
                )
        elif existing is not None:
            self._remove_state_files()

        verification = self.install_manager.verify(execute_binary=True)
        if not verification.valid:
            raise InstallationError(
                "Installation OpenCode invalide: " + "; ".join(verification.errors)
            )

        requested_workdir = (
            (workspace or self.layout.integration_root).expanduser().absolute()
        )
        if requested_workdir.is_symlink():
            raise ProcessManagerError("Un workspace symbolique est interdit")
        workdir = ensure_within(
            requested_workdir.resolve(), requested_workdir, must_exist=True
        )
        if not workdir.is_dir():
            raise ProcessManagerError("Le workspace OpenCode doit être un répertoire")

        port = self._allocate_port()
        password = secrets.token_urlsafe(32)
        instance_id = uuid4().hex
        environment = build_child_environment(
            self.layout,
            username=self.settings.username,
            password=password,
            source=os.environ,
            explicit=explicit_environment,
            additional_allowlist=additional_environment_allowlist,
            runtime_config_overlay=normalized_overlay,
        )
        command = [
            str(self.layout.binary_path),
            "serve",
            "--pure",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        state = ProcessState(
            pid=-1,
            port=port,
            hostname="127.0.0.1",
            binary_path=str(self.layout.binary_path.resolve()),
            workspace=str(workdir),
            instance_id=instance_id,
            version=self.manifest.version,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        stdout_path = self.layout.logs_dir / "server.stdout.log"
        stderr_path = self.layout.logs_dir / "server.stderr.log"
        stdout_fd = self._open_private_log(stdout_path)
        try:
            stderr_fd = self._open_private_log(stderr_path)
        except Exception:
            os.close(stdout_fd)
            raise
        kwargs: dict[str, Any] = {
            "cwd": str(workdir),
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_fd,
            "stderr": stderr_fd,
            "close_fds": True,
        }
        kwargs.update(descriptor_kwargs)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise ProcessManagerError("Impossible de lancer opencode serve") from exc
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
        self._process = process
        state = ProcessState(**{**asdict(state), "pid": process.pid})
        try:
            atomic_write_json(self.layout.process_state_path, asdict(state))
            atomic_write_json(
                self.layout.auth_state_path,
                {
                    "instance_id": instance_id,
                    "password": password,
                    "username": self.settings.username,
                },
            )
        except Exception as exc:
            self._signal_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except (AttributeError, subprocess.TimeoutExpired):
                self._signal_group(process.pid, signal.SIGKILL)
            self._remove_state_files()
            self._process = None
            raise ProcessManagerError(
                "Impossible de persister l'état privé OpenCode"
            ) from exc

        deadline = time.monotonic() + self.settings.startup_timeout_seconds
        last = HealthReport(False, None, None, "startup")
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._remove_state_files()
                raise ProcessManagerError(
                    f"opencode serve s'est arrêté avec le code {process.returncode}"
                )
            last = check_health(
                state.base_url,
                username=self.settings.username,
                password=password,
                expected_version=self.manifest.version,
                timeout_seconds=min(1.0, self.settings.request_timeout_seconds),
            )
            if last.healthy:
                return state
            time.sleep(0.1)
        try:
            self.stop()
        except ProcessManagerError:
            pass
        raise ProcessManagerError(
            f"Health-check OpenCode expiré ({last.error_code or 'unknown'})"
        )

    def status(self) -> ProcessStatus:
        state = self._read_state(optional=True)
        if state is None:
            return ProcessStatus(False, False, False, None, None, None, "not_started")
        if not self._pid_alive(state.pid):
            self._remove_state_files()
            return ProcessStatus(False, False, False, None, None, None, "stale_state")
        auth = self._read_auth(state)
        report = check_health(
            state.base_url,
            username=auth["username"],
            password=auth["password"],
            expected_version=self.manifest.version,
            timeout_seconds=min(2.0, self.settings.request_timeout_seconds),
        )
        owned = self._owns_process(state, auth, health_authenticated=report.healthy)
        return ProcessStatus(
            running=True,
            healthy=report.healthy,
            owned=owned,
            pid=state.pid,
            port=state.port,
            version=report.version,
            error_code=report.error_code,
        )

    def health(self) -> HealthReport:
        state = self._read_state(optional=False)
        assert state is not None
        auth = self._read_auth(state)
        return check_health(
            state.base_url,
            username=auth["username"],
            password=auth["password"],
            expected_version=self.manifest.version,
            timeout_seconds=self.settings.request_timeout_seconds,
        )

    def stop(self, *, force_after_timeout: bool = True) -> bool:
        state = self._read_state(optional=True)
        if state is None:
            return False
        if not self._pid_alive(state.pid):
            self._remove_state_files()
            return False
        auth = self._read_auth(state)
        report = check_health(
            state.base_url,
            username=auth["username"],
            password=auth["password"],
            expected_version=self.manifest.version,
            timeout_seconds=min(1.0, self.settings.request_timeout_seconds),
        )
        if not self._owns_process(state, auth, health_authenticated=report.healthy):
            raise ProcessOwnershipError(
                "Refus d'arrêter un processus dont l'appartenance n'est pas prouvée"
            )
        self._signal_group(state.pid, signal.SIGTERM)
        deadline = time.monotonic() + self.settings.shutdown_timeout_seconds
        while time.monotonic() < deadline and self._pid_alive(state.pid):
            time.sleep(0.1)
        if self._pid_alive(state.pid):
            if not force_after_timeout:
                raise ProcessManagerError("Le processus OpenCode ne s'est pas arrêté")
            self._signal_group(state.pid, signal.SIGKILL)
            kill_deadline = time.monotonic() + 2.0
            while time.monotonic() < kill_deadline and self._pid_alive(state.pid):
                time.sleep(0.05)
            if self._pid_alive(state.pid):
                raise ProcessManagerError(
                    "Impossible d'arrêter le groupe de processus OpenCode"
                )
        self._remove_state_files()
        self._process = None
        return True

    def cleanup_orphan(self) -> bool:
        state = self._read_state(optional=True)
        if state is None:
            return False
        if not self._pid_alive(state.pid):
            self._remove_state_files()
            return True
        current = self.status()
        if current.healthy:
            return False
        if not current.owned:
            raise ProcessOwnershipError(
                "Processus orphelin non attribuable: nettoyage refusé"
            )
        return self.stop()

    def auth_credentials(self) -> tuple[str, str, str]:
        """Retourne les credentials au client backend, jamais à la sortie CLI."""

        state = self._read_state(optional=False)
        assert state is not None
        auth = self._read_auth(state)
        return state.base_url, auth["username"], auth["password"]

    def _read_state(self, *, optional: bool) -> ProcessState | None:
        value = read_json_object(self.layout.process_state_path)
        if not value:
            if optional:
                return None
            raise ProcessManagerError("Serveur OpenCode non démarré")
        return ProcessState.from_mapping(value)

    def _read_auth(self, state: ProcessState) -> dict[str, str]:
        value = read_json_object(self.layout.auth_state_path)
        if value.get("instance_id") != state.instance_id:
            raise ProcessOwnershipError("État d'authentification OpenCode incohérent")
        username = value.get("username")
        password = value.get("password")
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or not password
        ):
            raise ProcessOwnershipError("Credentials OpenCode absents")
        return {
            "username": username,
            "password": password,
            "instance_id": state.instance_id,
        }

    def _owns_process(
        self,
        state: ProcessState,
        auth: Mapping[str, str],
        *,
        health_authenticated: bool,
    ) -> bool:
        if auth.get("instance_id") != state.instance_id:
            return False
        if Path(state.binary_path).resolve() != self.layout.binary_path.resolve():
            return False
        if self._process is not None and self._process.pid == state.pid:
            return True
        command = self._command_line(state.pid)
        if command is not None:
            expected = (
                str(self.layout.binary_path.resolve()),
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(state.port),
            )
            return all(fragment in command for fragment in expected)
        return health_authenticated and os.name == "nt"

    def _command_line(self, pid: int) -> str | None:
        proc_cmdline = Path(f"/proc/{pid}/cmdline")
        if proc_cmdline.exists():
            try:
                return (
                    proc_cmdline.read_bytes()
                    .replace(b"\x00", b" ")
                    .decode("utf-8", "replace")
                )
            except OSError:
                return None
        if os.name == "nt":
            return None
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    @staticmethod
    def _allocate_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _pid_alive(self, pid: int) -> bool:
        if self._process is not None and self._process.pid == pid:
            return self._process.poll() is None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _signal_group(self, pid: int, sig: signal.Signals) -> None:
        try:
            if os.name == "nt":
                command = ["taskkill", "/PID", str(pid), "/T"]
                if sig == signal.SIGKILL:
                    command.append("/F")
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    timeout=5,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key.upper()
                        in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}
                    },
                )
                if completed.returncode not in {0, 128}:
                    raise ProcessManagerError(
                        "taskkill n'a pas arrêté le groupe OpenCode"
                    )
            else:
                os.killpg(pid, sig)
        except (FileNotFoundError, ProcessLookupError):
            return

    @staticmethod
    def _open_private_log(path: Path) -> int:
        if path.is_symlink():
            raise ProcessManagerError(f"Log symbolique interdit: {path}")
        flags = os.O_CREAT | os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags, 0o600)

    def _remove_state_files(self) -> None:
        for path in (self.layout.process_state_path, self.layout.auth_state_path):
            if path.is_symlink():
                raise ProcessManagerError(f"État symbolique interdit: {path}")
            path.unlink(missing_ok=True)

    def debug_command(self, state: ProcessState) -> str:
        """Commande diagnostic sans secret."""

        return redact_text(
            f"{state.binary_path} serve --hostname 127.0.0.1 --port {state.port}"
        )
