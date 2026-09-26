from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_release import (
    REQUIRED_V02_RELEASE_FILES,
    build,
    project_version,
    tracked_files,
)
from release_metadata import parse_requirements_lock
from verify_release import verify_archive, verify_directory


class ReleaseV02IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        out = Path(cls.temp.name) / "g502x-onboard-0.2.0"
        cls.root, cls.archive = build(out)
        cls.manifest = json.loads(
            (cls.root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8")
        )
        cls.sbom = json.loads(
            (cls.root / "SBOM.spdx.json").read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_project_version_is_v020(self):
        self.assertEqual(project_version(), "0.2.0")
        self.assertEqual(self.manifest["version"], "0.2.0")
        self.assertEqual(self.manifest["release_name"], "g502x-onboard-0.2.0")

    def test_required_v02_release_surface_is_tracked_and_packaged(self):
        tracked = {path.as_posix() for path in tracked_files()}
        self.assertTrue(REQUIRED_V02_RELEASE_FILES.issubset(tracked))
        packaged = set(self.manifest["files"])
        self.assertTrue(REQUIRED_V02_RELEASE_FILES.issubset(packaged))
        self.assertEqual(
            self.manifest["entrypoints"],
            {"cli": "g502x.py", "tui": "g502x_tui.py"},
        )

    def test_manifest_records_two_exact_hash_locked_dependency_inputs(self):
        requirements = self.manifest["python_requirements"]
        self.assertEqual(set(requirements), {"core", "optional_tui"})

        core = requirements["core"]
        optional = requirements["optional_tui"]
        self.assertTrue(core["hash_checking"])
        self.assertTrue(optional["hash_checking"])
        self.assertEqual(core["path"], "requirements.txt")
        self.assertEqual(optional["path"], "requirements-tui.txt")

        core_lock = (self.root / core["path"]).read_text(encoding="utf-8")
        tui_lock = (self.root / optional["path"]).read_text(encoding="utf-8")
        self.assertEqual(core["packages"], parse_requirements_lock(core_lock))
        self.assertEqual(optional["packages"], parse_requirements_lock(tui_lock))
        self.assertEqual(core["packages"], {"hid": "1.0.9"})
        self.assertEqual(optional["packages"]["textual"], "8.2.8")
        self.assertEqual(set(core["packages"]) & set(optional["packages"]), set())

    def test_spdx_distinguishes_core_and_optional_tui_dependencies(self):
        rows = {
            row["SPDXID"]: row
            for row in self.sbom["packages"]
            if isinstance(row, dict)
        }
        relationships = {
            (
                row["spdxElementId"],
                row["relationshipType"],
                row["relatedSpdxElement"],
            )
            for row in self.sbom["relationships"]
        }
        root = "SPDXRef-Package-g502x-onboard"
        self.assertEqual(rows[root]["versionInfo"], "0.2.0")

        core = self.manifest["python_requirements"]["core"]["packages"]
        optional = self.manifest["python_requirements"]["optional_tui"]["packages"]
        for name, version in core.items():
            dep = "SPDXRef-Package-pypi-" + name.replace("_", "-")
            self.assertEqual(rows[dep]["versionInfo"], version)
            self.assertIn((root, "DEPENDS_ON", dep), relationships)

        for name, version in optional.items():
            dep = "SPDXRef-Package-pypi-" + name.replace("_", "-")
            self.assertEqual(rows[dep]["versionInfo"], version)
            self.assertIn((dep, "OPTIONAL_DEPENDENCY_OF", root), relationships)
            self.assertIn("Optional Textual UI", rows[dep]["comment"])

    def test_release_verifier_accepts_v2_directory_and_archive(self):
        verify_directory(self.root)
        verify_archive(self.archive)

    def test_archive_contains_tui_entrypoint_lock_and_release_notes(self):
        with zipfile.ZipFile(self.archive, "r") as zf:
            names = set(zf.namelist())
        prefix = "g502x-onboard-0.2.0/"
        for rel in (
            "g502x_tui.py",
            "requirements-tui.txt",
            "g502x_onboard/tui/app.py",
            "g502x_onboard/tui/runner.py",
            "tools/extracted_release_smoke.py",
            "docs/RELEASE_NOTES_V0.2.0.md",
        ):
            self.assertIn(prefix + rel, names)

    def test_wrong_release_tag_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "tag/version mismatch"):
                build(
                    Path(td) / "wrong-tag",
                    expected_tag="v0.1.0",
                )


if __name__ == "__main__":
    unittest.main()
