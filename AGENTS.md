# AGENTS.md

This repository controls persistent onboard configuration on real hardware. Automated contributors must treat the existing safety boundary as product authority, not as an implementation detail to optimize away.

## Authority and startup

Before changing code or documentation:

1. Fetch and inspect the current `origin/main` state and confirm the expected baseline for the task.
2. Read `README.md`, `SECURITY.md`, `SUPPORTED_DEVICES.md`, `CONTRIBUTING.md`, this file, and every architecture/safety document relevant to the change.
3. Inspect the implementation and tests that enforce the affected behavior.
4. Prefer the most conservative interpretation when documentation and code leave room for choice. Stop rather than inventing hardware behavior.

Repository state, committed evidence, and tests are authoritative. Chat history is not.

## Safety invariants

The following invariants are non-negotiable unless a separately approved, hardware-evidenced change explicitly changes the project safety model:

- Profile 1 / sector 1 is protected recovery state.
- Sectors 6 and 7 are protected.
- Normal persistent writes are restricted to Sector 0, Profiles 2-5, and macro sectors 8-15.
- No mouse firmware, receiver firmware, DFU state, or unrelated HID++ feature writes.
- Unknown devices, transports, firmware revisions, or unstable identities remain read-only.
- Persistent writes require stable exact-unit identity and a matching active local baseline.
- G HUB and Logitech Onboard Memory Manager host guards remain authoritative and fail closed where currently specified.
- Readback/reconciliation is authoritative after writes, including ambiguous transport outcomes.
- Post-write validation is mandatory.
- No force, unsafe, bypass, or override path may weaken the above gates.
- The `v0.1.0` tag and release are immutable historical artifacts.

Never broaden hardware support from descriptor similarity, external reports, or a convenient test fixture alone.

## Architectural rule for v0.2.0

The v0.2.0 terminal UI is an adapter, not a second hardware implementation.

- CLI and TUI must call one shared application/operations layer.
- `tui/**` must not import `hid`, `libs.*`, HID++ transport modules, or `g502x_onboard.device` directly.
- The real hardware backend remains synchronous. TUI responsiveness is provided by workers around application operations, not by making HID access asynchronous.
- The application layer owns operation serialization, safety gates, prepared-operation revalidation, write transaction semantics, and privacy classification.
- UI state must never be the sole authority for a hardware write. Backend/application checks remain authoritative.

See `docs/TUI_ARCHITECTURE.md`, `docs/TUI_SAFETY_MODEL.md`, and `docs/TUI_TEST_PLAN.md` before implementing TUI product code.

## Change discipline

Keep changes bounded to the requested milestone. Do not opportunistically change command workflows, hardware support, persistent-write semantics, recovery behavior, dependency policy, release history, or safety wording outside the task.

For behavior changes, add offline regression tests before relying on hardware. For documentation-only milestones, run the existing offline suite and validate that the docs do not claim support beyond current evidence.

Never commit private hardware state, including unit IDs, serial numbers, private fingerprints, local baselines/backups, raw sectors, private `probe --json`, `debug export` output, or personal macro/profile contents.
