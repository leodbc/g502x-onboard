# v0.2.0 roadmap

Status: bounded implementation roadmap following the architecture/safety design milestone. This roadmap does not itself change product code, requirements, dependencies, workflows, or hardware support.

## Release theme

v0.2.0 adds a terminal UI without creating a second hardware control path. The release is complete only when CLI and TUI are thin adapters over one shared application/operations layer and every existing hardware safety invariant remains authoritative.

## Sequencing

### Phase 0 - design authority

Deliver and review:

- `AGENTS.md`;
- `docs/TUI_ARCHITECTURE.md`;
- `docs/TUI_SAFETY_MODEL.md`;
- `docs/TUI_TEST_PLAN.md`;
- this roadmap.

No product code, requirements, or workflows change in Phase 0.

### Phase 1 - shared application contracts

Introduce typed operation/result/privacy models and a synchronous backend protocol. Wrap existing hardware behavior in `RealBackend` without changing write ordering, guard semantics, recovery behavior, or CLI output intent.

Add `FakeBackend` and contract tests first. Keep existing CLI commands on their current behavior until individual operations are migrated with parity tests.

Exit condition: application contracts can execute representative read-only and persistent workflows against FakeBackend while current CLI regressions remain green.

### Phase 2 - migrate CLI orchestration

Move CLI hardware orchestration behind the shared operations layer in bounded slices. Preserve command names, arguments, confirmation phrases, private/shareable output semantics, exit behavior, operation locking, and validation.

No TUI is required for this phase.

Exit condition: CLI adapter contains formatting/input concerns, not duplicated write authority, and parity/regression tests prove unchanged safety semantics.

### Phase 3 - prepared persistent operations

Implement the prepare -> review -> typed confirmation -> revalidate -> execute state machine in the shared layer.

Persistent operations must bind their reviewed target, revalidate under the operation lock, and become non-cancellable from the first potentially persistent backend action through reconciliation/post-validation.

Exit condition: the full failure/revalidation/cancellation matrix in `TUI_TEST_PLAN.md` passes against FakeBackend.

### Phase 4 - TUI state engine

Implement explicit Model/Update/View-inspired state independently of Textual widgets. Define typed events/effects, operation ids, phase transitions, privacy labels, stale-event rejection, and non-cancellable write presentation.

Exit condition: state/update tests cover all legal safety transitions without launching a terminal or importing HID code.

### Phase 5 - optional Textual adapter

Add the Textual UI as an optional dependency surface with lazy import. The TUI invokes synchronous shared operations only through workers.

Implement keyboard-first navigation, explicit refresh, 80x24 constrained layout, `NO_COLOR` semantics, review/confirmation flow, privacy-safe/private diagnostic presentation, and operation result views.

No background hardware polling is permitted.

Exit condition: Textual harness tests pass with and without `NO_COLOR`, at 80x24, keyboard-only, and with scripted worker faults.

### Phase 6 - integration and release hardening

Run complete offline tests and the existing CI matrix, adversarially review imports and write paths, update user-facing documentation for the optional TUI, and perform read-only smoke on validated hardware if available.

Physical persistent-write testing is required only if implementation changes persistent semantics. If such a change is discovered, stop and split it into a separately evidenced milestone rather than absorbing it into TUI work.

## v0.2.0 definition of done

v0.2.0 is done only when all of the following are true:

- CLI and TUI are adapters over one shared application/operations layer.
- `g502x_onboard/tui/**` has no low-level HID, `libs.*`, or direct `g502x_onboard.device` imports, enforced by tests.
- Real hardware access remains synchronous behind the application backend; the TUI uses workers solely for UI responsiveness.
- Only one hardware operation can run at a time, with both in-process coordination and the existing cross-process operation lock.
- Persistent destructive operations use a prepared-operation model with deterministic reviewed intent.
- Typed confirmation is required for destructive persistent operations.
- All persistent write authority is revalidated after human review and immediately before writes.
- A prepared operation is invalidated, not overridden, when baseline/device/host/recovery/target state changes.
- From the first potentially persistent write onward, the in-process transaction is non-cancellable until reconciliation and post-write validation reach a terminal result.
- Fresh readback/reconciliation remains authoritative for ambiguous writes.
- Post-write validation remains mandatory.
- There is no background hardware polling.
- Unknown devices/transports/firmware/unstable identities remain read-only in both UI affordances and application enforcement.
- Privacy-safe/shareable data is typed and kept distinct from private diagnostics; shareable exports pass the existing privacy validator.
- The TUI is fully operable by keyboard, preserves safety-critical content at 80x24, and remains semantically usable with `NO_COLOR`.
- Textual is optional and lazily imported; existing CLI/core use works without it.
- FakeBackend covers normal paths, guard failures, stale preparation, hot-swap, ambiguous commit, reconciliation, post-validation failure, operation serialization, and cancellation boundaries.
- Existing CLI workflows, confirmations, release checks, and supported-device claims remain regression-tested.
- The existing Linux x64, Windows x64, and Windows x86 offline CI lanes pass.
- `v0.1.0` and its release/tag remain untouched.

## Explicit non-goals for v0.2.0

The release does not include:

- support for additional mice, transports, firmware revisions, or host write targets without separate evidence;
- writes to Profile 1, sectors 6/7, firmware, receiver firmware, DFU, or arbitrary HID++ features;
- unsafe/force overrides;
- a daemon, service, tray app, resident process, or automatic/background device polling;
- asynchronous HID transport or concurrent hardware transactions;
- GUI/web UI, remote control, network API, plugin system, scripting engine, or multi-device management;
- automatic sharing of private diagnostics;
- redesign of the configuration format or existing CLI workflows merely to suit TUI layout;
- modification of historical v0.1.0 artifacts.

## Stop conditions during implementation

Stop the TUI milestone and open a separate design/evidence change if implementation appears to require any of the following:

- weakening identity, baseline, host, recovery, readback, or validation gates;
- changing the current persistent-write order or recovery behavior to fit the UI;
- adding writes to unsupported hardware/state;
- introducing a force/unsafe bypass;
- making background polling necessary for correctness;
- requiring Textual for existing CLI/core operation;
- changing current hardware support claims without new evidence.
