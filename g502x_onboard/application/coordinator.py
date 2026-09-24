from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


class OperationBusyError(RuntimeError):
    """Raised when a second in-process operation would overlap hardware work."""


class OperationCoordinator:
    """Synchronous, fail-closed in-process operation serialization."""

    def __init__(self) -> None:
        self._lock = Lock()

    @contextmanager
    def claim(self) -> Iterator[None]:
        if not self._lock.acquire(blocking=False):
            raise OperationBusyError("another hardware-facing operation is already active")
        try:
            yield
        finally:
            self._lock.release()
