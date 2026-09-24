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

Introduce typed operation/result/privacy models, the public application facade, a synchronous backend protocol, and an application composition root/factory. Wrap existing hardware behavior in `RealBackend` without changing write ordering, guard semantics, recovery behavior, or CLI output intent.

Add `FakeBackend` and contract tests first. The public facade may expose read-only/volatile operations and persistent **preparation/model** contracts, but Phase 1 must not expose a public "execute persistent write now" method that an adapter could call around the future prepared-operation protocol. Internal backend-parity tests may exercise preserved write primitives directly while they are being wrapped.

Keep existing CLI persistent commands on their current proven path until the prepared persistent protocol is ready.

Exit condition: application contracts can execute representative read-only/volatile workflows and build representative persistent preparations against FakeBackend; preserved persistent primitives have parity tests; no adapter-callable direct persistent bypass exists; current CLI regressions remain green.

### Phase 2 - migrate non-persistent CLI orchestration and shared infrastructure

Move read-only, planning, reporting, baseline-administration, and other non-persistent CLI orchestration behind the public application facade in bounded slices. Migrate shared locking/result/privacy infrastructure needed by persistent operations, but keep the current CLI persistent write entry points on their proven path until Phase 3 can replace them atomically.

Preserve command names, arguments, confirmation phrases, private/shareable/local-sensitive output semantics, exit behavior, operation locking, and validation. No TUI is required for this phase.

Exit condition: migrated CLI operations contain formatting/input concerns rather than duplicated hardware authority, parity/regression tests prove unchanged behavior, and there is still no public direct persistent-execute bypass.

### Phase 3 - prepared persistent operations and persistent CLI migration

Implement the prepare -> review -> typed confirmation -> revalidate -> execute state machine in the shared layer, then migrate `apply`, backup restore, and baseline restore to that protocol as one bounded safety change.

Persistent operations must bind their reviewed target, revalidate under the operation lock, and reject cooperative UI cancellation from the first potentially persistent backend action through reconciliation/post-validation. This must not be implemented by swallowing process-level interruption or changing current CLI `KeyboardInterrupt`/exit semantics.

Exit condition: CLI persistent commands and the future TUI path have exactly one adapter-callable persistent authority path, and the full failure/revalidation/cooperative-cancellation matrix in `TUI_TEST_PLAN.md` passes against FakeBackend.

### Phase 4 - TUI state engine

Implement explicit Model/Update/View-inspired state independently of Textual widgets. Define typed events/effects, operation ids, phase transitions, privacy labels, stale-event rejection, and non-cancellable write presentation.

Exit condition: state/update tests cover all legal safety transitions without launching a terminal or importing HID code.

### Phase 5 - optional Textual adapter

Add the Textual UI as an optional dependency surface with lazy import. The TUI invokes synchronous shared operations only through workers and only through the public application facade.

Add a dedicated exact-pinned, hash-locked optional dependency input/equivalent for Textual and all shipped transitive dependencies while keeping the core `requirements.txt` Textual-free.

Implement keyboard-first navigation, explicit refresh, 80x24 constrained layout, `NO_COLOR` semantics, review/confirmation flow, privacy-safe/local-sensitive/private-diagnostic presentation, and operation result views.

No background hardware polling, CLI-spawn escape hatch, or direct backend construction is permitted.

Exit condition: Textual harness tests pass with and without `NO_COLOR`, at 80x24, keyboard-only, with scripted worker faults, with static facade-boundary tests, and with an explicit no-Textual core/CLI test.

### Phase 6 - integration and release hardening

Run complete offline tests and the expanded CI matrix, adversarially review imports/write paths/adapter escape hatches, update user-facing documentation for the optional TUI, and perform read-only smoke on validated hardware if available.

Update release machinery so v0.2.0 release inputs, manifest, SPDX SBOM, deterministic ZIP, extracted-archive smoke, version/tag checks, and release notes all account for the TUI and its locked optional dependency set. Preserve a separate no-Textual core/CLI lane and the existing Linux x64, Windows x64, and Windows x86 reproducibility evidence.

Physical persistent-write testing is required only if implementation changes persistent semantics. If such a change is discovered, stop and split it into a separately evidenced milestone rather than absorbing it into TUI work.

## v0.2.0 definition of done

v0.2.0 is done only when all of the following are true:

- CLI and TUI are adapters over one shared public application/operations facade; neither adapter constructs/calls `RealBackend` or hardware primitives directly.
- `g502x_onboard/tui/**` has no low-level HID, `libs.*`, direct `g502x_onboard.device`, `application.backend`, CLI-spawn, `runpy`, or dynamic-import hardware escape path, enforced by static/runtime boundary tests.
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
- Privacy-safe/shareable, local-sensitive/user-authored, and private-diagnostic data are distinct typed classes; shareable exports pass the existing privacy validator and review data cannot be accidentally promoted to shareable.
- The TUI is fully operable by keyboard, preserves safety-critical content at 80x24, and remains semantically usable with `NO_COLOR`.
- Textual is optional and lazily imported; existing CLI/core use works without it.
- Textual and every shipped optional transitive dependency are exactly pinned/hash-locked in a reproducible optional dependency input, and v0.2.0 release manifest/SBOM metadata represents that dependency set.
- FakeBackend covers normal paths, guard failures, stale preparation, hot-swap, ambiguous commit, reconciliation, post-validation failure, operation serialization, and cancellation boundaries.
- Existing CLI workflows, confirmations, process-level interruption/exit behavior, release checks, and supported-device claims remain regression-tested.
- The existing Linux x64, Windows x64, and Windows x86 offline CI lanes pass, plus explicit no-Textual core/CLI and locked-Textual TUI lanes.
- The deterministic v0.2.0 release artifact contains a runnable TUI surface and optional dependency metadata, passes extracted-artifact smoke in both core-only and TUI-enabled modes, and uses v0.2.0-appropriate version/tag/release notes.
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
