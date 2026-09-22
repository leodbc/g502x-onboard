from __future__ import annotations

import csv
import io
import subprocess
import sys
from typing import Any, Callable


def _process_token(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def windows_process_names_matching(
    fragment: str,
    *,
    platform: str | None = None,
    run: Callable[..., Any] | None = None,
) -> list[str]:
    """Return Windows process image names containing the requested fragment.

    On Windows this is a safety primitive: inability to execute or parse
    tasklist fails closed instead of being interpreted as no process.
    Non-Windows hosts return an empty list because G HUB is not a supported
    direct-write competitor there.
    """
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return []

    needle = str(fragment).strip().lower()
    if not needle:
        raise ValueError("process-name fragment must be non-empty")
    compact_needle = _process_token(needle)

    runner = subprocess.run if run is None else run
    try:
        result = runner(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            errors="replace",
        )
    except Exception as exc:
        raise RuntimeError(
            "unable to verify Windows process state; tasklist execution failed"
        ) from exc

    returncode = int(getattr(result, "returncode", 1))
    if returncode != 0:
        detail = str(getattr(result, "stderr", "") or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            "unable to verify Windows process state; "
            f"tasklist exited with code {returncode}{suffix}"
        )

    stdout = getattr(result, "stdout", "")
    if not isinstance(stdout, str):
        raise RuntimeError(
            "unable to verify Windows process state; tasklist output is not text"
        )

    try:
        rows = csv.reader(io.StringIO(stdout))
        names = {
            row[0].strip()
            for row in rows
            if row
            and row[0].strip()
            and (
                needle in row[0].lower()
                or compact_needle in _process_token(row[0])
            )
        }
    except Exception as exc:
        raise RuntimeError(
            "unable to verify Windows process state; "
            "tasklist output could not be parsed"
        ) from exc

    return sorted(names)
