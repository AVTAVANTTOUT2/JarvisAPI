"""Protocol asynchrone que tout runtime agentique doit implémenter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from .models import (
    AgenticContext,
    AgenticRun,
    ApprovalRequest,
    Artifact,
    RuntimeEvent,
    RuntimeHealth,
    ToolCapability,
)


@runtime_checkable
class AgenticRuntime(Protocol):
    """Frontière provider-neutral possédée par JARVIS."""

    runtime_id: str

    @property
    def capabilities(self) -> Sequence[ToolCapability]: ...

    async def health(self) -> RuntimeHealth: ...

    async def create_run(self, run: AgenticRun, context: AgenticContext) -> str | None:
        """Provisionne une session et retourne son identifiant opaque éventuel."""
        ...

    async def start(self, run: AgenticRun) -> None: ...

    async def pause(self, run_id: str) -> None: ...

    async def resume(self, run_id: str) -> None: ...

    async def cancel(self, run_id: str) -> None: ...

    async def answer_approval(
        self,
        run_id: str,
        approval: ApprovalRequest,
    ) -> None: ...

    def stream_events(self, run_id: str) -> AsyncIterator[RuntimeEvent]: ...

    async def get_artifacts(self, run_id: str) -> Sequence[Artifact]: ...

    async def dispose(self) -> None: ...


__all__ = ["AgenticRuntime"]
