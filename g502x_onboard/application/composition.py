from __future__ import annotations

from .real_backend import RealBackend
from .facade import ApplicationFacade


def create_application() -> ApplicationFacade:
    """Construct the real application while keeping RealBackend adapter-internal."""
    return ApplicationFacade(RealBackend())
