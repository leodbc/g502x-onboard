from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import PreparationContext, ProbeSnapshot, ValidationSnapshot


@runtime_checkable
class Backend(Protocol):
    """Synchronous, use-case-shaped application/backend boundary."""

    def probe(self) -> ProbeSnapshot: ...

    def validate(self) -> ValidationSnapshot: ...

    def switch_profile_guarded(self, target: int) -> int: ...

    def preparation_context(self) -> PreparationContext: ...
