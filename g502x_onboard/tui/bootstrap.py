from __future__ import annotations

import argparse
import sys
from typing import Callable


_OPTIONAL_IMPORT_ROOTS = {
    "textual",
    "rich",
    "markdown_it",
    "mdit_py_plugins",
    "linkify_it",
    "uc_micro",
    "mdurl",
    "platformdirs",
    "pygments",
    "typing_extensions",
}


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="g502x_tui.py",
        description="Optional full-screen Textual adapter for G502 X Onboard.",
    )


def _load_app_class():
    from .app import G502XTuiApp

    return G502XTuiApp


def main(
    argv: list[str] | None = None,
    *,
    application_factory: Callable[[], object] | None = None,
    app_loader: Callable[[], type] | None = None,
) -> int:
    _parser().parse_args(argv)
    loader = app_loader or _load_app_class
    try:
        app_class = loader()
    except ModuleNotFoundError as exc:
        root = (exc.name or "").split(".", 1)[0]
        if root not in _OPTIONAL_IMPORT_ROOTS:
            raise
        print(
            "Optional Textual UI dependencies are not installed. "
            "Install the core lock and then requirements-tui.txt.",
            file=sys.stderr,
        )
        return 2

    if application_factory is None:
        from g502x_onboard.application import create_application

        application_factory = create_application

    facade = application_factory()
    app = app_class(facade=facade)
    app.run()
    return 0
