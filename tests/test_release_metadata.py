from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from release_metadata import build_spdx, parse_requirements_lock
from libs.utils import WINDOWS_HIDAPI_SHA256


class ReleaseMetadataLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock_text = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_current_lock_is_accepted(self):
        packages = parse_requirements_lock(self.lock_text)
        self.assertTrue(packages)
        self.assertIn("hid", packages)
        self.assertEqual(set(packages), {"hid"})

    def test_missing_require_hashes_fails_closed(self):
        weakened = self.lock_text.replace("--require-hashes\n", "", 1)
        with self.assertRaisesRegex(ValueError, "require-hashes"):
            parse_requirements_lock(weakened)

    def test_sha1_substitution_fails_closed(self):
        weakened = self.lock_text.replace(
            "--hash=sha256:",
            "--hash=sha1:",
            1,
        )
        with self.assertRaisesRegex(ValueError, "unsupported hash algorithm"):
            parse_requirements_lock(weakened)

    def test_malformed_sha256_fails_closed(self):
        marker = "--hash=sha256:"
        before, sep, after = self.lock_text.partition(marker)
        self.assertTrue(sep)
        digest, rest = after.split(None, 1)
        weakened = before + marker + "not-a-sha256 " + rest
        with self.assertRaisesRegex(ValueError, "invalid sha256"):
            parse_requirements_lock(weakened)

    def test_unpinned_requirement_fails_closed(self):
        weakened = self.lock_text.replace("hid==1.0.9", "hid>=1.0.9", 1)
        with self.assertRaisesRegex(ValueError, "exactly pinned"):
            parse_requirements_lock(weakened)

    def test_empty_lock_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_requirements_lock("--require-hashes\n")


    def test_runtime_pins_actual_dlls_and_sbom_agrees(self):
        self.assertEqual(set(WINDOWS_HIDAPI_SHA256), {"x64", "x86"})
        files = {
            arch: ROOT / "libs" / arch / "hidapi.dll"
            for arch in ("x64", "x86")
        }
        for arch, path in files.items():
            self.assertTrue(path.is_file(), path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, WINDOWS_HIDAPI_SHA256[arch])

        sbom = build_spdx(
            version="0.1.0",
            source_commit="a" * 40,
            source_timestamp="2026-09-22T00:00:00+00:00",
            requirements_text=self.lock_text,
            hidapi_files=files,
        )
        sbom_hashes = {
            row["fileName"].split("/")[-2]:
                row["checksums"][0]["checksumValue"]
            for row in sbom["files"]
            if row["fileName"].endswith("/hidapi.dll")
        }
        self.assertEqual(sbom_hashes, WINDOWS_HIDAPI_SHA256)
        self.assertEqual(sbom["name"], "g502x-onboard-0.1.0")
        self.assertEqual(
            sbom["documentNamespace"],
            "https://spdx.org/spdxdocs/g502x-onboard-" + ("a" * 40),
        )


    def test_spdx_identifies_vendored_hidapi_0150(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            x64 = root / "x64.dll"
            x86 = root / "x86.dll"
            x64.write_bytes(b"x64")
            x86.write_bytes(b"x86")
            sbom = build_spdx(
                version="0.1.0",
                source_commit="a" * 40,
                source_timestamp="2026-09-22T00:00:00+00:00",
                requirements_text=self.lock_text,
                hidapi_files={"x64": x64, "x86": x86},
            )
            hidapi = next(
                p for p in sbom["packages"]
                if p["SPDXID"] == "SPDXRef-Package-vendored-hidapi"
            )
            self.assertEqual(hidapi["versionInfo"], "0.15.0")
            self.assertIn("hidapi-0.15.0", hidapi["downloadLocation"])


if __name__ == "__main__":
    unittest.main()
