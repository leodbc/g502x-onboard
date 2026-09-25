from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import g502x_onboard.cli as cli
from g502x_onboard.application.models import (
    OperationResult,
    PrivacyClass,
    StatusSnapshot,
)
from g502x_onboard.constants import (
    GLOBAL_MACRO_SECTORS,
    PROGRAMMABLE_PROFILES,
    RECOVERY_SECTORS,
)


class _StatusApplication:
    def __init__(self, snapshot: StatusSnapshot):
        self.snapshot = snapshot
        self.calls: list[bool] = []

    def status(self, *, private: bool):
        self.calls.append(private)
        return OperationResult(
            ok=True,
            value=self.snapshot,
            privacy=self.snapshot.privacy,
        )


def _summary(*, private: bool) -> dict:
    enabled = (1, 2, 3)
    profiles = {}
    for profile in PROGRAMMABLE_PROFILES:
        row = {
            "enabled": profile in enabled,
            "crc_ok": True,
        }
        if private:
            row["metadata"] = {
                "name": f"SECRET_PROFILE_{profile}",
                "polling_rate_hz": 1000,
            }
        profiles[str(profile)] = row

    macro_pages = {}
    for offset, sector in enumerate(GLOBAL_MACRO_SECTORS, start=1):
        row = {
            "health": "crc_valid",
            "crc_ok": True,
            "erased": False,
        }
        if private:
            row.update(
                payload_non_ff=offset,
                high_water=offset * 2,
                payload_capacity=253,
            )
        macro_pages[str(sector)] = row

    return {
        "ok": True,
        "error_count": 0,
        "warning_count": 0,
        "enabled_profiles": list(enabled),
        "recovery": {str(sector): True for sector in RECOVERY_SECTORS},
        "profiles": profiles,
        "macro_pages": macro_pages,
        "referenced_macro_starts": 0,
    }


def _snapshot(*, private: bool) -> StatusSnapshot:
    return StatusSnapshot(
        active_profile=1,
        descriptor={"profile_format": 3, "macro_format": 1},
        summary=_summary(private=private),
        enabled_profiles=(1, 2, 3),
        privacy=(
            PrivacyClass.LOCAL_SENSITIVE
            if private
            else PrivacyClass.SHAREABLE
        ),
    )


def _render(*, private: bool, json_output: bool) -> tuple[str, _StatusApplication]:
    app = _StatusApplication(_snapshot(private=private))
    args = SimpleNamespace(private=private, json=json_output)
    out = StringIO()
    with patch.object(cli, "create_application", return_value=app):
        with redirect_stdout(out):
            cli.cmd_status(args)
    return out.getvalue(), app


class StatusRendererPrivacyTests(unittest.TestCase):
    def test_public_text_renders_structural_macro_health_without_private_metrics(self):
        output, app = _render(private=False, json_output=False)

        self.assertEqual(app.calls, [False])
        for sector in GLOBAL_MACRO_SECTORS:
            self.assertIn(f"s{sector}: state=crc_valid", output)
        self.assertNotIn("payload_non-FF=", output)
        self.assertNotIn("high-water=", output)
        self.assertNotIn("payload_non_ff", output)
        self.assertNotIn("high_water", output)
        self.assertNotIn("SECRET_PROFILE_", output)
        self.assertIn("metadata=<redacted>", output)

    def test_private_text_retains_macro_utilization_details(self):
        output, app = _render(private=True, json_output=False)

        self.assertEqual(app.calls, [True])
        self.assertIn("payload_non-FF=", output)
        self.assertIn("high-water=", output)
        self.assertIn("SECRET_PROFILE_2", output)

    def test_public_json_emits_public_summary_as_is(self):
        expected = _summary(private=False)
        output, app = _render(private=False, json_output=True)

        self.assertEqual(app.calls, [False])
        self.assertEqual(json.loads(output), expected)
        self.assertNotIn("payload_non_ff", output)
        self.assertNotIn("high_water", output)
        self.assertNotIn("SECRET_PROFILE_", output)

    def test_private_json_emits_private_summary_as_is(self):
        expected = _summary(private=True)
        output, app = _render(private=True, json_output=True)

        self.assertEqual(app.calls, [True])
        self.assertEqual(json.loads(output), expected)
        self.assertIn("payload_non_ff", output)
        self.assertIn("high_water", output)
        self.assertIn("SECRET_PROFILE_2", output)

    def test_phase3_live_public_summary_shape_does_not_require_private_metrics(self):
        summary = _summary(private=False)
        self.assertEqual(summary["enabled_profiles"], [1, 2, 3])
        for row in summary["macro_pages"].values():
            self.assertEqual(set(row), {"health", "crc_ok", "erased"})

        output, _app = _render(private=False, json_output=False)

        self.assertIn("Structural validation: PASS", output)
        self.assertNotIn("payload_non_ff", output)
        self.assertNotIn("high_water", output)


if __name__ == "__main__":
    unittest.main()
