from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import sys
import unittest
from typing import get_args, get_type_hints

from g502x_onboard.application.models import PersistentPhase, PrivacyClass


ROOT = Path(__file__).resolve().parents[1]
TUI = ROOT / "g502x_onboard" / "tui"
PURE_FILES = {
    "__init__.py",
    "model.py",
    "events.py",
    "effects.py",
    "update.py",
    "view.py",
}
ADAPTER_FILES = {"app.py", "bootstrap.py", "runner.py"}


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class TuiArchitectureTests(unittest.TestCase):
    def tui_sources(self):
        return sorted(TUI.rglob("*.py"))

    def test_pure_phase4_files_remain_textual_and_worker_free(self):
        forbidden_roots = {
            "asyncio",
            "concurrent",
            "hid",
            "importlib",
            "libs",
            "multiprocessing",
            "rich",
            "subprocess",
            "textual",
            "threading",
        }
        forbidden_names = {"ApplicationFacade", "RealBackend", "FakeBackend"}
        for path in self.tui_sources():
            if path.name not in PURE_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".", 1)[0], forbidden_roots, path)
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".", 1)[0]
                    self.assertNotIn(root, forbidden_roots, path)
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, (path, alias.name))
                elif isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_names, (path, node.id))

    def test_all_tui_sources_forbid_hardware_and_cli_escape_hatches(self):
        forbidden_prefixes = (
            "g502x_onboard.device",
            "g502x_onboard.application.backend",
            "g502x_onboard.application.real_backend",
            "g502x_onboard.application.fake_backend",
            "libs",
            "hid",
        )
        forbidden_calls = {
            "__import__",
            "builtins.__import__",
            "eval",
            "exec",
            "importlib.import_module",
            "os.popen",
            "os.system",
            "runpy.run_module",
            "runpy.run_path",
            "subprocess.Popen",
            "subprocess.run",
        }
        forbidden_names = {"RealBackend", "FakeBackend"}
        for path in self.tui_sources():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("g502x.py", source, path)
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(
                            alias.name.startswith(forbidden_prefixes), (path, alias.name)
                        )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(module.startswith(forbidden_prefixes), (path, module))
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_names, (path, alias.name))
                elif isinstance(node, ast.Name):
                    self.assertNotIn(node.id, forbidden_names, (path, node.id))
                elif isinstance(node, ast.Call):
                    self.assertNotIn(_call_name(node.func), forbidden_calls, path)

    def test_adapter_uses_textual_workers_not_custom_thread_scheduler(self):
        app_source = (TUI / "app.py").read_text(encoding="utf-8")
        self.assertIn("run_worker(", app_source)
        self.assertIn("thread=True", app_source)
        for path in self.tui_sources():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("ThreadPoolExecutor", source, path)
            self.assertNotRegex(source, r"\bThread\s*\(", path)

    def test_create_application_is_confined_to_bootstrap_boundary(self):
        importers = []
        for path in self.tui_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name == "create_application":
                            importers.append(path.name)
        self.assertEqual(importers, ["bootstrap.py"])

    def test_application_facade_calls_are_confined_to_runner(self):
        offenders = []
        for path in self.tui_sources():
            source = path.read_text(encoding="utf-8")
            if "ApplicationFacade" in source and path.name != "runner.py":
                offenders.append(path.name)
            if "._facade." in source and path.name != "runner.py":
                offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_canonical_persistent_types_are_reused(self):
        from g502x_onboard.tui.model import ForegroundOperation
        from g502x_onboard.tui.events import ChangePrivacySurface

        phase_type = get_type_hints(ForegroundOperation)["phase"]
        self.assertIn(PersistentPhase, get_args(phase_type))
        self.assertIs(get_type_hints(ChangePrivacySurface)["privacy"], PrivacyClass)

    def test_importing_tui_does_not_require_textual_or_rich(self):
        code = r"""
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "textual" or name.startswith("textual.") or name == "rich" or name.startswith("rich."):
        raise AssertionError("optional UI dependency imported")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import g502x_onboard.tui
print("PURE_TUI_IMPORT_OK")
"""
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
        code = r"""
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "textual" or name.startswith("textual.") or name == "rich" or name.startswith("rich."):
        raise AssertionError("optional UI dependency imported")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import g502x_onboard.cli
print("CORE_CLI_IMPORT_OK")
"""
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CORE_CLI_IMPORT_OK", proc.stdout)

    def test_tui_help_is_stdlib_only_and_hardware_free(self):
        proc = subprocess.run(
            [sys.executable, "g502x_tui.py", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("optional full-screen textual adapter", proc.stdout.lower())

    def test_missing_textual_fails_before_application_factory(self):
        from g502x_onboard.tui import bootstrap

        calls = []

        def factory():
            calls.append("factory")
            raise AssertionError("application factory must not be called")

        def missing_loader():
            exc = ModuleNotFoundError("No module named 'textual'")
            exc.name = "textual"
            raise exc

        rc = bootstrap.main([], application_factory=factory, app_loader=missing_loader)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])

    def test_core_requirements_remain_textual_free(self):
        core = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotRegex(core, r"(?m)^\s*textual\b")
        self.assertNotRegex(core, r"(?m)^\s*rich\b")

    def test_optional_lock_is_exact_and_hash_locked(self):
        lock = (ROOT / "requirements-tui.txt").read_text(encoding="utf-8")
        self.assertIn("--require-hashes", lock)
        logical = re.sub(r"\\\n\s*", " ", lock)
        rows = [
            row.strip()
            for row in logical.splitlines()
            if row.strip() and not row.lstrip().startswith("--")
        ]
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            self.assertRegex(row, r"^[A-Za-z0-9_.-]+==[^\s]+\s+")
            self.assertIn("--hash=sha256:", row)
            requirement = row.split()[0]
            self.assertEqual(requirement.count("=="), 1)
            name, version = requirement.split("==", 1)
            self.assertTrue(name and version)
            self.assertNotRegex(version, r"[<>~=!]")

    def test_effect_values_carry_no_live_authority(self):
        from dataclasses import fields
        from g502x_onboard.tui import effects

        effect_types = [
            effects.StartForegroundOperation,
            effects.PreparePersistentOperation,
            effects.ExecutePreparedOperation,
            effects.RequestCooperativeCancellation,
            effects.RequestExplicitRefresh,
        ]
        forbidden = {"facade", "backend", "device", "callable", "handle", "thread", "worker"}
        for effect_type in effect_types:
            names = {field.name.lower() for field in fields(effect_type)}
            self.assertTrue(names.isdisjoint(forbidden), (effect_type.__name__, names))


if __name__ == "__main__":
    unittest.main()
