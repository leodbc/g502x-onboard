from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import unittest
from typing import get_args, get_type_hints

from g502x_onboard.application.models import PersistentPhase, PrivacyClass


ROOT = Path(__file__).resolve().parents[1]
TUI = ROOT / "g502x_onboard" / "tui"


class TuiArchitectureTests(unittest.TestCase):
    def tui_sources(self):
        return sorted(TUI.glob("*.py"))

    def test_phase4_package_uses_no_forbidden_import_or_escape_hatch(self):
        forbidden_import_roots = {
            "hid", "libs", "subprocess", "runpy", "importlib", "textual", "rich"
        }
        forbidden_import_prefixes = (
            "g502x_onboard.device",
            "g502x_onboard.application.backend",
            "g502x_onboard.application.real_backend",
            "g502x_onboard.application.fake_backend",
        )
        forbidden_names = {
            "RealBackend", "FakeBackend", "ApplicationFacade", "__import__"
        }
        forbidden_calls = {
            "os.system",
            "subprocess.run",
            "subprocess.Popen",
            "runpy.run_module",
            "runpy.run_path",
            "importlib.import_module",
        }
        for path in self.tui_sources():
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".", 1)[0]
                        self.assertNotIn(
                            root, forbidden_import_roots, (path, alias.name)
                        )
                        self.assertFalse(
                            alias.name.startswith(forbidden_import_prefixes),
                            (path, alias.name),
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    root = module.split(".", 1)[0]
                    self.assertNotIn(
                        root, forbidden_import_roots, (path, module)
                    )
                    self.assertFalse(
                        module.startswith(forbidden_import_prefixes),
                        (path, module),
                    )
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name, forbidden_names, (path, alias.name)
                        )
                elif isinstance(node, ast.Name):
                    self.assertNotIn(
                        node.id, forbidden_names, (path, node.id)
                    )
                elif isinstance(node, ast.Call):
                    target = self._call_name(node.func)
                    self.assertNotIn(
                        target, forbidden_calls, (path, target)
                    )

    def _call_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = self._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    def test_no_thread_worker_terminal_or_cli_launch_symbols(self):
        forbidden = {
            "Thread",
            "ThreadPoolExecutor",
            "asyncio",
            "worker",
            "Worker",
            "g502x.py",
        }
        for path in self.tui_sources():
            source = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, source, (path, token))

    def test_canonical_persistent_phase_and_privacy_enums_are_not_redefined(self):
        class_names = set()
        for path in self.tui_sources():
            tree = ast.parse(
                path.read_text(encoding="utf-8"), filename=str(path)
            )
            class_names.update(
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
            )
        self.assertNotIn("PersistentPhase", class_names)
        self.assertNotIn("PrivacyClass", class_names)
        from g502x_onboard.tui.model import ForegroundOperation
        from g502x_onboard.tui.events import ChangePrivacySurface

        phase_type = get_type_hints(ForegroundOperation)["phase"]
        self.assertIn(PersistentPhase, get_args(phase_type))
        self.assertIs(
            get_type_hints(ChangePrivacySurface)["privacy"],
            PrivacyClass,
        )

    def test_importing_tui_does_not_require_textual_or_rich(self):
        code = r'''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "textual" or name.startswith("textual.") or name == "rich" or name.startswith("rich."):
        raise AssertionError("optional UI dependency imported")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import g502x_onboard.tui
print("PURE_TUI_IMPORT_OK")
'''
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PURE_TUI_IMPORT_OK", proc.stdout)

    def test_existing_cli_import_does_not_require_textual_or_rich(self):
        code = r'''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "textual" or name.startswith("textual.") or name == "rich" or name.startswith("rich."):
        raise AssertionError("optional UI dependency imported")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import g502x_onboard.cli
print("CORE_CLI_IMPORT_OK")
'''
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CORE_CLI_IMPORT_OK", proc.stdout)

    def test_existing_cli_help_still_runs(self):
        proc = subprocess.run(
            [sys.executable, "g502x.py", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage:", proc.stdout.lower())


    def test_effect_values_carry_no_callable_or_live_authority(self):
        from dataclasses import fields
        from g502x_onboard.tui import effects

        effect_types = [
            effects.StartForegroundOperation,
            effects.PreparePersistentOperation,
            effects.ExecutePreparedOperation,
            effects.RequestCooperativeCancellation,
            effects.RequestExplicitRefresh,
        ]
        forbidden = {
            "facade",
            "backend",
            "device",
            "callable",
            "handle",
            "thread",
            "worker",
        }
        for effect_type in effect_types:
            names = {
                field.name.lower() for field in fields(effect_type)
            }
            self.assertTrue(
                names.isdisjoint(forbidden),
                (effect_type.__name__, names),
            )


if __name__ == "__main__":
    unittest.main()
