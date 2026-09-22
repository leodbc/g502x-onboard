from __future__ import annotations

import hashlib
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

from verify_release import verify_archive, verify_directory


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture(root: Path) -> None:
    payload = b"payload\n"
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "documentNamespace": (
            "https://spdx.org/spdxdocs/test-" + "a" * 40
        ),
    }
    sbom_bytes = (
        json.dumps(sbom, sort_keys=True).encode("utf-8") + b"\n"
    )
    (root / "payload.txt").write_bytes(payload)
    (root / "SBOM.spdx.json").write_bytes(sbom_bytes)
    manifest = {
        "format": "g502x-release-v1",
        "source_commit": "a" * 40,
        "private_state_included": False,
        "archive_reproducible": True,
        "archive_layout": {
            "order": "lexicographic",
            "timestamp": "1980-01-01T00:00:00",
            "compression": "stored",
            "file_mode": "0644",
        },
        "python_requirements": {
            "hash_checking": True,
            "packages": {"hid": "1.0.9"},
        },
        "sbom": {
            "path": "SBOM.spdx.json",
            "format": "SPDX-2.3",
            "sha256": _sha(sbom_bytes),
        },
        "files": {
            "SBOM.spdx.json": {
                "sha256": _sha(sbom_bytes),
                "bytes": len(sbom_bytes),
            },
            "payload.txt": {
                "sha256": _sha(payload),
                "bytes": len(payload),
            },
        },
    }
    (root / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _canonical_zip(root: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in sorted(root.iterdir()):
            info = zipfile.ZipInfo(
                filename=f"release/{path.name}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes())


class ReleaseVerifierTests(unittest.TestCase):
    def test_directory_accepts_exact_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "release"
            root.mkdir()
            _fixture(root)
            verify_directory(root)

    def test_directory_rejects_extra_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "release"
            root.mkdir()
            _fixture(root)
            (root / "extra.txt").write_text("unexpected\n")
            with self.assertRaisesRegex(RuntimeError, "file set mismatch"):
                verify_directory(root)

    def test_directory_rejects_tampered_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "release"
            root.mkdir()
            _fixture(root)
            (root / "payload.txt").write_text("tampered\n")
            with self.assertRaisesRegex(RuntimeError, "size mismatch|sha256 mismatch"):
                verify_directory(root)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_directory_rejects_symlink_directory(self):
        if sys.platform.startswith("win"):
            self.skipTest("Windows symlink privilege/semantics vary")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "release"
            root.mkdir()
            _fixture(root)
            outside = base / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret\n")
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                verify_directory(root)

    def test_archive_rejects_noncanonical_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "release"
            root.mkdir()
            _fixture(root)
            archive = base / "bad-metadata.zip"
            with zipfile.ZipFile(
                archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as zf:
                for path in sorted(root.iterdir()):
                    zf.write(path, f"release/{path.name}")
            with self.assertRaisesRegex(
                RuntimeError,
                "not stored|non-canonical timestamp|non-canonical mode",
            ):
                verify_archive(archive)

    def test_archive_and_checksum_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "release"
            root.mkdir()
            _fixture(root)
            archive = base / "g502x-onboard-0.1.0.zip"
            _canonical_zip(root, archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_suffix(".zip.sha256").write_text(
                f"{digest}  {archive.name}\n",
                encoding="ascii",
            )
            verify_archive(archive)

            archive.with_suffix(".zip.sha256").write_text(
                f"{'0' * 64}  {archive.name}\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(RuntimeError, "checksum sidecar"):
                verify_archive(archive)

    def test_archive_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bad.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
                info = zipfile.ZipInfo(
                    filename="../escape.txt",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                zf.writestr(info, b"x")
            with self.assertRaisesRegex(RuntimeError, "unsafe ZIP member path|release root"):
                verify_archive(archive)


if __name__ == "__main__":
    unittest.main()
