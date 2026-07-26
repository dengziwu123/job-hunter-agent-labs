from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Iterator


@dataclass
class _LockEntry:
    lock: Lock
    users: int = 0


_registry_lock = Lock()
_workspace_locks: dict[str, _LockEntry] = {}


@contextmanager
def workspace_execution_lock(workspace_id: str) -> Iterator[None]:
    """Serialize state-changing execution for one local workspace."""
    with _registry_lock:
        entry = _workspace_locks.setdefault(workspace_id, _LockEntry(lock=Lock()))
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _registry_lock:
            entry.users -= 1
            if entry.users == 0 and _workspace_locks.get(workspace_id) is entry:
                del _workspace_locks[workspace_id]
