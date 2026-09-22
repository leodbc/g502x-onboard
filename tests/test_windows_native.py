from __future__ import annotations

import struct
import sys
import unittest


@unittest.skipUnless(
    sys.platform == "win32",
    "vendored Windows hidapi import is Windows-only",
)
class WindowsNativeHidapiImportTests(unittest.TestCase):
    def test_vendored_hidapi_import_is_release_matched(self):
        # Import itself is the gate: libs.LogiHPP20 verifies the resolved DLL
        # path, native hidapi version and architecture-specific SHA-256 before
        # exposing the transport.
        from libs import LogiHPP20 as transport

        self.assertEqual(transport.hid.version, (0, 15, 0))
        self.assertIn(struct.calcsize("P") * 8, (32, 64))
        self.assertIsNotNone(transport._HIDAPI_DLL_DIRECTORY_HANDLE)


if __name__ == "__main__":
    unittest.main()
