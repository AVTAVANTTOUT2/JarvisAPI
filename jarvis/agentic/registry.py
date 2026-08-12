"""Découverte dynamique des runtimes sous ``integrations/*/plugin.json``."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Callable

from .models import RiskLevel, RuntimePluginManifest, ToolCapability
from .runtime import AgenticRuntime


class RuntimePluginError(RuntimeError):
    pass


class RuntimePluginManifestError(RuntimePluginError):
    pass


_RUNTIME_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def _capability(value: Any) -> ToolCapability:
    if isinstance(value, str):
        return ToolCapability(name=value, scope=value)
    if not isinstance(value, dict):
        raise RuntimePluginManifestError("capability invalide")
    name = str(value.get("name") or value.get("id") or "").strip()
    scope = str(value.get("scope") or name).strip()
    if not name or not scope:
        raise RuntimePluginManifestError("capability name/scope requis")
    try:
        risk = RiskLevel(str(value.get("risk_level", "low")))
    except ValueError as exc:
        raise RuntimePluginManifestError(f"risk_level invalide pour {name}") from exc
    return ToolCapability(
        name=name,
        scope=scope,
        description=str(value.get("description") or ""),
        risk_level=risk,
        requires_approval=bool(value.get("requires_approval", False)),
        reversible=bool(value.get("reversible", True)),
        timeout_s=float(value.get("timeout_s", 60.0)),
    )


def _parse_manifest(path: Path) -> RuntimePluginManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimePluginManifestError(f"manifest illisible: {path}") from exc
    if not isinstance(raw, dict):
        raise RuntimePluginManifestError(f"manifest non objet: {path}")
    runtime = raw.get("runtime") if isinstance(raw.get("runtime"), dict) else raw
    runtime_id = str(
        runtime.get("id") or runtime.get("runtime_id") or raw.get("id") or ""
    ).strip()
    entrypoint = str(runtime.get("entrypoint") or raw.get("entrypoint") or "").strip()
    version = str(runtime.get("version") or raw.get("version") or "").strip()
    name = str(runtime.get("name") or raw.get("name") or runtime_id).strip()
    if not _RUNTIME_ID.fullmatch(runtime_id):
        raise RuntimePluginManifestError(f"runtime id invalide: {runtime_id!r}")
    if not entrypoint or ":" not in entrypoint:
        raise RuntimePluginManifestError(
            f"entrypoint attendu sous la forme module:factory pour {runtime_id}"
        )
    if not version:
        raise RuntimePluginManifestError(f"version requise pour {runtime_id}")
    capabilities_raw = runtime.get("capabilities", raw.get("capabilities", []))
    if not isinstance(capabilities_raw, list):
        raise RuntimePluginManifestError("capabilities doit être une liste")
    known = {
        "id",
        "runtime_id",
        "name",
        "version",
        "entrypoint",
        "capabilities",
        "enabled",
        "manifest_version",
        "runtime",
    }
    return RuntimePluginManifest(
        runtime_id=runtime_id,
        name=name or runtime_id,
        version=version,
        entrypoint=entrypoint,
        root=path.parent,
        capabilities=tuple(_capability(item) for item in capabilities_raw),
        enabled=bool(runtime.get("enabled", raw.get("enabled", True))),
        manifest_version=int(raw.get("manifest_version", 1)),
        metadata={key: value for key, value in raw.items() if key not in known},
    )


def discover_runtime_plugins(
    integrations_root: str | Path | None = None,
) -> tuple[RuntimePluginManifest, ...]:
    """Découvre et valide les manifests sans importer leur code."""

    root = (
        Path(integrations_root)
        if integrations_root is not None
        else Path(__file__).resolve().parents[2] / "integrations"
    ).resolve()
    if not root.is_dir():
        return ()
    manifests: dict[str, RuntimePluginManifest] = {}
    for path in sorted(root.glob("*/plugin.json")):
        manifest = _parse_manifest(path)
        if not manifest.enabled:
            continue
        if manifest.runtime_id in manifests:
            raise RuntimePluginManifestError(f"runtime dupliqué: {manifest.runtime_id}")
        manifests[manifest.runtime_id] = manifest
    return tuple(manifests.values())


def _load_file_module(manifest: RuntimePluginManifest, module_ref: str) -> ModuleType:
    relative_ref = (
        module_ref if module_ref.endswith(".py") else module_ref.replace(".", "/")
    )
    candidate = (manifest.root / relative_ref).resolve()
    if candidate.suffix != ".py":
        candidate = candidate.with_suffix(".py")
    try:
        candidate.relative_to(manifest.root)
    except ValueError as exc:
        raise RuntimePluginError("entrypoint hors du répertoire du plugin") from exc
    if not candidate.is_file():
        raise RuntimePluginError(f"module de runtime absent: {candidate.name}")
    module_name = f"jarvis_agentic_plugin_{manifest.runtime_id}_{abs(hash(candidate))}"
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimePluginError(f"module non chargeable: {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_runtime_factory(manifest: RuntimePluginManifest) -> Callable[..., Any]:
    module_ref, attribute = manifest.entrypoint.rsplit(":", 1)
    local_ref = module_ref.replace(".", "/")
    local_candidate = manifest.root / local_ref
    if module_ref.endswith(".py") or local_candidate.with_suffix(".py").is_file():
        module = _load_file_module(manifest, module_ref)
    else:
        module = importlib.import_module(module_ref)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise RuntimePluginError(f"factory introuvable: {manifest.entrypoint}")
    return factory


def _validate_runtime(runtime: Any, manifest: RuntimePluginManifest) -> AgenticRuntime:
    required = (
        "health",
        "create_run",
        "start",
        "pause",
        "resume",
        "cancel",
        "answer_approval",
        "stream_events",
        "get_artifacts",
        "dispose",
    )
    missing = [name for name in required if not callable(getattr(runtime, name, None))]
    if missing:
        raise RuntimePluginError(
            f"runtime {manifest.runtime_id} incomplet: {', '.join(missing)}"
        )
    if str(getattr(runtime, "runtime_id", "")) != manifest.runtime_id:
        raise RuntimePluginError("runtime_id de la factory différent du manifest")
    if not hasattr(runtime, "capabilities"):
        raise RuntimePluginError("capabilities absent du runtime")
    if any(not isinstance(item, ToolCapability) for item in runtime.capabilities):
        raise RuntimePluginError("capabilities du runtime non conformes")
    return runtime


class RuntimeRegistry:
    """Cache lazy des instances, sans échec d'import si aucun plugin n'existe."""

    def __init__(
        self,
        manifests: tuple[RuntimePluginManifest, ...] | None = None,
        *,
        integrations_root: str | Path | None = None,
    ) -> None:
        selected = (
            manifests
            if manifests is not None
            else discover_runtime_plugins(integrations_root)
        )
        self._manifests = {manifest.runtime_id: manifest for manifest in selected}
        self._instances: dict[str, AgenticRuntime] = {}
        self._lock = asyncio.Lock()

    @property
    def manifests(self) -> tuple[RuntimePluginManifest, ...]:
        return tuple(self._manifests.values())

    def manifest(self, runtime_id: str) -> RuntimePluginManifest | None:
        return self._manifests.get(runtime_id)

    async def get(self, runtime_id: str) -> AgenticRuntime | None:
        manifest = self._manifests.get(runtime_id)
        if manifest is None:
            return None
        cached = self._instances.get(runtime_id)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._instances.get(runtime_id)
            if cached is not None:
                return cached
            factory = load_runtime_factory(manifest)
            signature = inspect.signature(factory)
            accepts_manifest = "manifest" in signature.parameters or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            created = factory(manifest=manifest) if accepts_manifest else factory()
            if inspect.isawaitable(created):
                created = await created
            runtime = _validate_runtime(created, manifest)
            self._instances[runtime_id] = runtime
            return runtime

    async def dispose(self) -> None:
        instances = tuple(self._instances.values())
        self._instances.clear()
        for runtime in instances:
            await runtime.dispose()


__all__ = [
    "RuntimePluginError",
    "RuntimePluginManifestError",
    "RuntimeRegistry",
    "discover_runtime_plugins",
    "load_runtime_factory",
]
