# G502 X Onboard v0.2.0

These are release notes prepared for the v0.2.0 candidate. They do not indicate that the tag or GitHub Release has already been published.

## What changed since v0.1.0

- CLI and the optional terminal UI now share the same public application authority instead of maintaining separate hardware orchestration paths.
- Persistent apply/restore flows use prepared operations with review, exact typed confirmation, execution-time revalidation, reconciliation, and mandatory post-write validation.
- An optional Textual terminal UI adds keyboard-first operation, explicit refresh, 80x24-aware constrained presentation, privacy-class-aware views, and clear non-cancellable presentation from WRITING onward.
- Textual remains optional. The core CLI uses `requirements.txt`; the TUI uses the separate exact-pinned, hash-locked `requirements-tui.txt`.
- Release manifest and SPDX SBOM metadata now distinguish the core dependency set from the optional TUI dependency set.
- Release CI validates deterministic packaging plus extracted-artifact operation in both core-only/no-Textual and locked-TUI environments.

## Safety boundary

v0.2.0 does not broaden persistent-write hardware support. The supported write target remains the validated G502 X LIGHTSPEED scope documented in `SUPPORTED_DEVICES.md`.

Profile 1 / sector 1 and sectors 6/7 remain protected recovery state. The release does not add firmware, receiver-firmware, DFU, unrelated HID++ writes, force/unsafe overrides, or background hardware polling.

The TUI is an adapter over the same application authority as the CLI. UI state is not write authority, and worker transport failure during WRITING/RECONCILING/POST_VALIDATING does not manufacture terminal hardware truth.

## Installation

Core only:

```bash
python -m pip install -r requirements.txt
python g502x.py --version
```

Optional TUI:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-tui.txt
python g502x_tui.py
```

The final read-only physical TUI release smoke completed successfully on the merged v0.2.0 candidate. The smoke exercised TUI startup, one explicit read-only hardware refresh, privacy-safe presentation, keyboard navigation, and clean exit with zero persistent transactions, zero device writes, zero profile switches, and zero firmware/DFU operations.
