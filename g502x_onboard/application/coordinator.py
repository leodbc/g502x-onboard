from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


_PROCESS_LOCK = Lock()


class OperationBusyError(RuntimeError):
    """Raised when a second in-process operation would overlap hardware work."""


class OperationCoordinator:
    """Synchronous, fail-closed in-process operation serialization."""

    def __init__(self) -> None:
        # Every coordinator instance shares one process-local claim. This keeps
        # independently constructed facades from creating parallel hardware
        # lanes while leaving the OS-backed lock as the cross-process authority.
        self._lock = _PROCESS_LOCK

    @contextmanager
    def claim(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise OperationBusyError("another hardware-facing operation is already active")
        try:
            yield
        finally:
            self._lock.release()
