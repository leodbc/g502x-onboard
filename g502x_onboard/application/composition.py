from __future__ import annotations

from .facade import ApplicationFacade


def create_application() -> ApplicationFacade:
    """Construct the real application without exporting backend authority."""
    # Keep the concrete backend out of module globals so the public factory does
    # not itself become a convenient reachability path to RealBackend.
    from .real_backend import RealBackend

    return ApplicationFacade(RealBackend())
