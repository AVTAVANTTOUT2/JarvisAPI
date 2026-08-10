from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agents.engineering_team.models import TeamState


class CycleAlreadyRunning(RuntimeError):
    pass


class StateStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.state_path = runtime_dir / "state.json"
        self.lock_path = runtime_dir / "cycle.lock"

    def load(self) -> TeamState:
        if not self.state_path.exists():
            return TeamState()
        return TeamState.from_dict(
            json.loads(self.state_path.read_text(encoding="utf-8"))
        )

    def save(self, state: TeamState) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.runtime_dir, delete=False
        ) as handle:
            handle.write(serialized)
            temp_path = Path(handle.name)
        os.replace(temp_path, self.state_path)

    @contextmanager
    def cycle_lock(self) -> Iterator[None]:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CycleAlreadyRunning(
                    "un cycle d'ingénierie est déjà actif"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
