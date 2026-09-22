from __future__ import annotations

from pathlib import Path

from .baseline import active_baseline


ROOT = Path(__file__).resolve().parents[1]


def require_baseline() -> None:
    active_baseline()


def baseline_map() -> dict[int, bytes]:
    images, _manifest = active_baseline()
    return images


def baseline_sector(sector: int) -> bytes:
    images = baseline_map()
    if sector not in images:
        raise RuntimeError(f"baseline sector missing: {sector}")
    return images[sector]
