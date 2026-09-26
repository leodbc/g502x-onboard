#!/usr/bin/env python3
"""Software-only smoke tests against an extracted release surface."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def _run(
    args: list[str],
    *,
    cwd: Path,
    expected_returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected_returncode:
        raise RuntimeError(
            f"command failed ({result.returncode} != {expected_returncode}): "
            + " ".join(args)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _run_in_fresh_environment(root: Path, mode: str) -> None:
    with tempfile.TemporaryDirectory(prefix="g502x-release-env-") as td:
        env_root = Path(td) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(env_root)
        python = _venv_python(env_root)
        install = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(root / "requirements.txt"),
        ]
        if mode == "tui":
            install.extend(["-r", str(root / "requirements-tui.txt")])
        _run(install, cwd=Path(td))

        extracted_smoke = root / "tools" / "extracted_release_smoke.py"
        _run(
            [
                str(python),
                str(extracted_smoke),
                str(root),
                "--mode",
                mode,
            ],
            cwd=Path(td),
        )


def _assert_optional_absent() -> None:
    for module in ("textual", "rich"):
        if importlib.util.find_spec(module) is not None:
            raise RuntimeError(f"core-only smoke unexpectedly has optional module: {module}")


def _assert_optional_present() -> None:
    for module in ("textual", "rich"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"TUI smoke is missing locked optional module: {module}")


def _core_smoke(root: Path) -> None:
    _assert_optional_absent()
    with tempfile.TemporaryDirectory(prefix="g502x-release-core-") as td:
        cwd = Path(td)
        version = _run(
            [sys.executable, str(root / "g502x.py"), "--version"],
            cwd=cwd,
        )
        if "0.2.0" not in version.stdout:
            raise RuntimeError("extracted CLI did not report v0.2.0")

        selftest = _run(
            [sys.executable, str(root / "g502x.py"), "selftest"],
            cwd=cwd,
        )
        if "PASS" not in selftest.stdout:
            raise RuntimeError("extracted core selftest did not PASS")

        capabilities = _run(
            [sys.executable, str(root / "g502x.py"), "capabilities", "--json"],
            cwd=cwd,
        )
        payload = json.loads(capabilities.stdout)
        if not isinstance(payload, list) or not payload:
            raise RuntimeError("extracted capabilities output is not a non-empty list")

        help_result = _run(
            [sys.executable, str(root / "g502x_tui.py"), "--help"],
            cwd=cwd,
        )
        if "optional full-screen textual adapter" not in help_result.stdout.lower():
            raise RuntimeError("extracted TUI help is unavailable without Textual")

        missing = _run(
            [sys.executable, str(root / "g502x_tui.py")],
            cwd=cwd,
            expected_returncode=2,
        )
        if "optional textual ui dependencies are not installed" not in missing.stderr.lower():
            raise RuntimeError("missing-TUI dependency guidance is not clean/fail-closed")


def _tui_smoke(root: Path) -> None:
    _assert_optional_present()
    with tempfile.TemporaryDirectory(prefix="g502x-release-tui-") as td:
        cwd = Path(td)
        help_result = _run(
            [sys.executable, str(root / "g502x_tui.py"), "--help"],
            cwd=cwd,
        )
        if "optional full-screen textual adapter" not in help_result.stdout.lower():
            raise RuntimeError("extracted TUI help failed")

        code = (
            "import sys; "
            f"sys.path.insert(0, {str(root)!r}); "
            "from g502x_onboard.tui.bootstrap import _load_app_class; "
            "cls=_load_app_class(); "
            "assert cls.__name__ == 'G502XTuiApp'; "
            "print('EXTRACTED_TUI_IMPORT_PASS')"
        )
        imported = _run([sys.executable, "-c", code], cwd=cwd)
        if "EXTRACTED_TUI_IMPORT_PASS" not in imported.stdout:
            raise RuntimeError("extracted locked-TUI import smoke failed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke an extracted G502 X Onboard release without hardware access."
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--mode", choices=("core", "tui"), required=True)
    parser.add_argument(
        "--fresh-env",
        action="store_true",
        help="create a fresh virtualenv and install only locks from the extracted release",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"release root not found: {root}")

    for required in (
        "g502x.py",
        "g502x_tui.py",
        "requirements.txt",
        "requirements-tui.txt",
        "tools/extracted_release_smoke.py",
    ):
        if not (root / required).is_file():
            raise SystemExit(f"release smoke input missing: {required}")

    if args.fresh_env:
        _run_in_fresh_environment(root, args.mode)
    elif args.mode == "core":
        _core_smoke(root)
    else:
        _tui_smoke(root)

    print(f"EXTRACTED RELEASE {args.mode.upper()} SMOKE: PASS")


if __name__ == "__main__":
    main()
