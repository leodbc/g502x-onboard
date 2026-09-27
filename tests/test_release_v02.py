from __future__ import annotations

import hashlib
import json
import shutil
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
    deterministic_zip,
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

    def _copy_release(self, base: Path, name: str = "g502x-onboard-0.2.0") -> Path:
        root = base / name
        shutil.copytree(self.root, root)
        return root

    @staticmethod
    def _write_manifest(root: Path, manifest: dict) -> None:
        (root / "RELEASE_MANIFEST.json").write_bytes(
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )

    def _mutate_sbom(self, root: Path, mutate) -> None:
        sbom_path = root / "SBOM.spdx.json"
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        mutate(sbom)
        sbom_bytes = (
            json.dumps(sbom, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        sbom_path.write_bytes(sbom_bytes)

        manifest_path = root / "RELEASE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(sbom_bytes).hexdigest()
        manifest["sbom"]["sha256"] = digest
        manifest["files"]["SBOM.spdx.json"]["sha256"] = digest
        manifest["files"]["SBOM.spdx.json"]["bytes"] = len(sbom_bytes)
        self._write_manifest(root, manifest)

    @staticmethod
    def _write_reverse_order_zip(root: Path, archive: Path) -> None:
        files = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
            reverse=True,
        )
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            for path in files:
                arcname = (Path(root.name) / path.relative_to(root)).as_posix()
                info = zipfile.ZipInfo(
                    filename=arcname,
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.create_system = 3
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, path.read_bytes())

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

    def test_v2_directory_cannot_downgrade_to_v1(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))
            manifest_path = root / "RELEASE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["format"] = "g502x-release-v1"
            self._write_manifest(root, manifest)
            with self.assertRaisesRegex(RuntimeError, "historical v1 manifest"):
                verify_directory(root)

    def test_v2_zip_cannot_use_v1_style_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = self._copy_release(base)
            manifest_path = root / "RELEASE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["format"] = "g502x-release-v1"
            manifest["python_requirements"] = {
                "hash_checking": True,
                "packages": {"hid": "1.0.9"},
            }
            self._write_manifest(root, manifest)
            archive = base / "downgraded.zip"
            deterministic_zip(root, archive)
            with self.assertRaisesRegex(RuntimeError, "historical v1"):
                verify_archive(archive)

    def test_spdx_missing_vendored_hidapi_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))
            def mutate(sbom):
                removed = "SPDXRef-Package-vendored-hidapi"
                sbom["packages"] = [
                    row for row in sbom["packages"]
                    if row.get("SPDXID") != removed
                ]
                sbom["relationships"] = [
                    row for row in sbom["relationships"]
                    if row.get("spdxElementId") != removed
                    and row.get("relatedSpdxElement") != removed
                ]

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "vendored hidapi package"):
                verify_directory(root)

    def test_spdx_missing_vendored_dll_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))
            def mutate(sbom):
                removed = "SPDXRef-File-hidapi-x64"
                sbom["files"] = [
                    row for row in sbom["files"]
                    if row.get("SPDXID") != removed
                ]
                sbom["relationships"] = [
                    row for row in sbom["relationships"]
                    if row.get("spdxElementId") != removed
                    and row.get("relatedSpdxElement") != removed
                ]

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "file record mismatch"):
                verify_directory(root)

    def test_spdx_changed_vendored_dll_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                row = next(
                    row for row in sbom["files"]
                    if row.get("SPDXID") == "SPDXRef-File-hidapi-x64"
                )
                row["checksums"][0]["checksumValue"] = "0" * 64

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                verify_directory(root)

    def test_spdx_missing_root_to_hidapi_relationship_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["relationships"] = [
                    row for row in sbom["relationships"]
                    if not (
                        row.get("spdxElementId") == "SPDXRef-Package-g502x-onboard"
                        and row.get("relationshipType") == "DEPENDS_ON"
                        and row.get("relatedSpdxElement")
                        == "SPDXRef-Package-vendored-hidapi"
                    )
                ]

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "dependency relationship missing"):
                verify_directory(root)

    def test_spdx_missing_hidapi_containment_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["relationships"] = [
                    row for row in sbom["relationships"]
                    if not (
                        row.get("spdxElementId") == "SPDXRef-Package-vendored-hidapi"
                        and row.get("relationshipType") == "CONTAINS"
                        and row.get("relatedSpdxElement") == "SPDXRef-File-hidapi-x64"
                    )
                ]

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "containment relationship missing"):
                verify_directory(root)

    def test_spdx_duplicate_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["packages"].append(dict(sbom["packages"][0]))

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "duplicate SPDXID"):
                verify_directory(root)

    def test_spdx_canonical_package_name_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                original = next(
                    row for row in sbom["packages"]
                    if row.get("name") == "typing-extensions"
                )
                collision = dict(original)
                collision["SPDXID"] = "SPDXRef-Package-pypi-typing-extensions-collision"
                collision["name"] = "typing_extensions"
                sbom["packages"].append(collision)

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "canonical package-name collision"):
                verify_directory(root)

    def test_spdx_unexpected_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["packages"].append(
                    {
                        "SPDXID": "SPDXRef-Package-unexpected",
                        "name": "unexpected-package",
                        "versionInfo": "1.0",
                    }
                )

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "package ID set mismatch"):
                verify_directory(root)

    def test_spdx_unexpected_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["files"].append(
                    {
                        "SPDXID": "SPDXRef-File-unexpected",
                        "fileName": "./docs/unexpected.txt",
                        "checksums": [
                            {
                                "algorithm": "SHA256",
                                "checksumValue": "0" * 64,
                            }
                        ],
                    }
                )

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "file ID set mismatch"):
                verify_directory(root)

    def test_spdx_unexpected_package_and_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["packages"].append(
                    {
                        "SPDXID": "SPDXRef-Package-unexpected",
                        "name": "unexpected-package",
                        "versionInfo": "1.0",
                    }
                )
                sbom["files"].append(
                    {
                        "SPDXID": "SPDXRef-File-unexpected",
                        "fileName": "./docs/unexpected.txt",
                        "checksums": [
                            {
                                "algorithm": "SHA256",
                                "checksumValue": "0" * 64,
                            }
                        ],
                    }
                )

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "package ID set mismatch"):
                verify_directory(root)

    def test_spdx_duplicate_relationship_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["relationships"].append(dict(sbom["relationships"][0]))

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "duplicate relationship"):
                verify_directory(root)

    def test_spdx_document_describes_must_be_exact_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                root_id = "SPDXRef-Package-g502x-onboard"
                sbom["documentDescribes"] = [root_id, root_id]

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "documentDescribes"):
                verify_directory(root)

    def test_spdx_unexpected_relationship_between_known_ids_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["relationships"].append(
                    {
                        "spdxElementId": "SPDXRef-Package-g502x-onboard",
                        "relationshipType": "CONTAINS",
                        "relatedSpdxElement": "SPDXRef-File-hidapi-x64",
                    }
                )

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "relationship set mismatch"):
                verify_directory(root)

    def test_spdx_unknown_relationship_endpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._copy_release(Path(td))

            def mutate(sbom):
                sbom["relationships"].append(
                    {
                        "spdxElementId": "SPDXRef-Package-g502x-onboard",
                        "relationshipType": "DEPENDS_ON",
                        "relatedSpdxElement": "SPDXRef-Package-does-not-exist",
                    }
                )

            self._mutate_sbom(root, mutate)
            with self.assertRaisesRegex(RuntimeError, "unknown SPDXID"):
                verify_directory(root)

    def test_archive_rejects_reversed_physical_member_order(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "reversed.zip"
            self._write_reverse_order_zip(self.root, archive)
            with self.assertRaisesRegex(RuntimeError, "member order"):
                verify_archive(archive)

    def test_v2_archive_root_must_match_release_name(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            wrong_root = self._copy_release(base, name="wrong-safe-root")
            archive = base / "wrong-root.zip"
            deterministic_zip(wrong_root, archive)
            with self.assertRaisesRegex(RuntimeError, "ZIP root"):
                verify_archive(archive)

    def test_custom_output_directory_keeps_canonical_archive_root(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root, archive = build(base / "ci-release")
            self.assertEqual(root.name, "ci-release")
            with zipfile.ZipFile(archive, "r") as zf:
                roots = {
                    name.split("/", 1)[0]
                    for name in zf.namelist()
                }
            self.assertEqual(roots, {"g502x-onboard-0.2.0"})
            verify_archive(archive)

    def test_wrong_release_tag_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "tag/version mismatch"):
                build(
                    Path(td) / "wrong-tag",
                    expected_tag="v0.1.0",
                )


if __name__ == "__main__":
    unittest.main()
