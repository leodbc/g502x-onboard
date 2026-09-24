# Terminal UI test plan for v0.2.0

Status: required offline verification plan for the v0.2.0 TUI and shared application/operations layer. Hardware tests may supplement this plan but may not replace it.

## Test strategy

The TUI must be testable without a physical mouse. Most new behavior is exercised below the Textual rendering layer using a deterministic `FakeBackend`, explicit state/update tests, and adapter contract tests.

The existing compiler, validator, safety, release, and CLI tests remain required. v0.2.0 must not trade existing safety coverage for UI snapshots.

## FakeBackend architecture

The application layer defines a synchronous backend protocol used by both `RealBackend` and `FakeBackend`.

`FakeBackend` is an in-memory model, not a mock of Textual widgets. It should model:

- descriptor / compatibility class;
- stable or unstable unit identity;
- transport and firmware evidence;
- active baseline identity/digest;
- 16 sector images and active profile;
- host-guard state for G HUB and Onboard Memory Manager;
- programmable/recovery sector boundaries;
- read/write call history in strict order;
- deterministic operation latency hooks controlled by tests;
- fault injection before write, during write, after a potentially committed write, during fresh readback, and during post-validation;
- reconnect behavior and hot-swap identity changes;
- explicit counters for every hardware read/write so polling can be detected.

The fake must execute synchronously just like the real backend. Worker behavior is tested by running synchronous fake calls behind the same TUI effect/worker boundary.

Fixtures must never weaken application checks merely because the backend is fake.

## Layered test suite

### 1. Static architecture tests

Require:

- no `hid`, `libs.*`, HID++ transport, `g502x_onboard.device`, `application.backend`, or `RealBackend` imports anywhere under `g502x_onboard/tui/**`;
- TUI code calls hardware-affecting operations only through the public application facade;
- TUI code cannot shell/spawn the CLI or hardware commands and contains no `subprocess`/`os.system`/`runpy`/dynamic-import escape hatch to bypass that facade;
- backend construction occurs only in the approved application composition root/factory, never in TUI widgets/effects/screens;
- after Phase 2/3 migration, the CLI adapter has no direct low-level `.device`/backend path for migrated operations;
- importing/running the existing CLI succeeds when Textual is not installed;
- importing core application models does not import Textual;
- optional TUI bootstrap fails cleanly before hardware access when Textual is absent.

Use AST/import-graph checks plus a runtime facade/backend spy where practical; grep alone is insufficient because dynamic imports and subprocess escape hatches must also fail the architecture gate.

### 2. Application contract tests

For every operation, verify RealBackend/FakeBackend-facing orchestration semantics independently of presentation.

Persistent-operation tests must prove:

- prepare performs no persistent write;
- protected sectors are never present in an intended write set;
- unknown hardware cannot produce an executable prepared persistent operation;
- preparation captures plan/target and baseline bindings;
- typed-confirmation phrase is supplied by the operation contract;
- execution revalidates after review;
- baseline change after preparation fails before any write;
- device hot-swap/identity change fails before any further write;
- firmware/transport eligibility change fails closed;
- G HUB or Onboard Memory Manager becoming active after review fails before write;
- SAFE/recovery precondition change after review fails before write;
- changed config/plan/backup target invalidates preparation;
- no force/override path exists;
- readback/reconciliation determines ambiguous write outcomes;
- full post-write validation is performed.

### 3. One-operation-at-a-time tests

Use controlled blocking FakeBackend calls to prove:

- a second hardware operation is rejected while one is active;
- read-only refresh cannot overlap a persistent transaction;
- the in-process coordinator and OS-backed operation lock are both used;
- operation state is released after success and after terminal failure;
- presentation-only interactions can continue without backend calls.

### 4. Cancellation boundary tests

Drive the persistent phase machine deterministically.

Verify cooperative cancellation:

- succeeds in `PREPARED`, `REVIEWING`, `CONFIRMING`, `REVALIDATING`, and `ARMED` before the first persistent write;
- cannot stop the worker after the transition to `WRITING`;
- cannot replace the active operation with another operation;
- does not cause any reconciliation or success-path post-write validation that would otherwise run to be skipped;
- allows an independent terminal safety/backend failure to end the transaction without extra writes, while recording reconciliation/validation status;
- produces an explicit non-cancellable state in the model/view;
- still yields a terminal result after an Esc/back/worker-cancel request during write;
- never implements post-`WRITING` cancellation by killing a thread/process or raising an asynchronous exception.

Separately test process-level interruption as a failure mode rather than calling it cooperative cancellation: preserve existing CLI `KeyboardInterrupt`/exit behavior, and verify that an interruption/disconnect after a potentially persistent action is never reported as "unchanged" without authoritative reconciliation. Inject exceptions immediately before and immediately after the first potentially persistent backend call to lock down the exact boundary.

### 5. Model / Update / View tests

Treat `update` as a deterministic state machine wherever possible.

Cover:

- legal screen/operation phase transitions;
- stale worker events ignored by operation id;
- confirmation input reset when preparation changes;
- invalidation returns the user to a new-prepare path rather than preserving confirmation;
- local-sensitive and private-diagnostic classifications survive every state transition;
- errors do not silently drop an active non-cancellable transaction;
- view models contain explicit labels for read-only state, privacy class, and non-cancellable write phase.

Snapshots may test rendering, but safety assertions must be structural and semantic rather than pixel/color dependent.

### 6. No-background-polling tests

With a FakeBackend hardware-call counter:

1. render/mount the idle TUI;
2. advance Textual/test clocks and timers;
3. navigate/focus/open help without requesting refresh;
4. assert the backend call count remains unchanged.

Then trigger an explicit refresh and assert exactly the expected foreground operation occurs. Repeat while a persistent worker is active to prove timers do not create concurrent reads.

### 7. Unknown/read-only hardware tests

Script mismatched firmware, unsupported transport, unvalidated device model, missing stable identity, and incompatible geometry.

For each case:

- read-only probe/report paths remain available only as permitted by existing policy;
- persistent actions are disabled/absent with a textual reason;
- direct application invocation still refuses preparation/execution;
- no sector write is recorded.

### 8. Privacy tests

Maintain separate typed fixtures for privacy-safe/shareable, local-sensitive/user-authored, and private-diagnostic data.

Verify:

- shareable exports pass the existing public-report validator on the exact emitted bytes/data;
- unit IDs, serials, private fingerprints, raw sectors, baseline/backup contents, personal profile names/macros, and private/local paths never enter shareable view models;
- a plan/review containing profile names, bindings, or macros is classified local-sensitive rather than shareable or private-diagnostic;
- local-sensitive review data can be shown for the explicit local workflow without exposing device identity/raw baseline state;
- private diagnostic surfaces are visibly labeled before data is revealed;
- local-sensitive/private payloads are never placed in operation ids, worker names, exception titles, telemetry, or default shareable logs;
- switching from local-sensitive or private-diagnostic views to shareable views clears sensitive content rather than merely hiding the widget.

### 9. Keyboard-first and 80x24 tests

Using Textual's test harness once the optional dependency is installed:

- exercise every primary action without mouse input;
- prepare, review, confirm, cancel-before-write, inspect result, refresh, and navigate help using keyboard only;
- run at 80 columns x 24 rows and assert primary actions/safety labels remain reachable;
- assert no required safety content depends on horizontal scrolling;
- resize below the supported minimum and show an explicit constrained-layout message rather than corrupting confirmation controls.

### 10. `NO_COLOR` tests

Set `NO_COLOR` and verify:

- semantic state is still expressed in text/symbols/labels;
- warning/error/success/read-only/private/non-cancellable states remain distinguishable in the view model;
- snapshots do not require color to determine meaning;
- the flag does not change hardware behavior or safety decisions.

### 11. CLI/TUI parity tests

For the same FakeBackend state and operation inputs, compare normalized application results reached through CLI and TUI adapters.

Parity must cover at least plan/apply, validate/status, profile selection, restore, baseline restore, and privacy-safe report generation. Adapter formatting may differ; operation intent, gates, write order, reconciliation, and result classification may not.

Existing CLI confirmation phrases and behavior remain regression-tested.

### 12. Failure/reconciliation matrix

For each persistent write stage inject:

- pre-write exception;
- write call returns failure with unchanged bytes;
- write call raises after target bytes actually committed;
- fresh readback equals target;
- fresh readback equals previous bytes;
- fresh readback matches neither previous nor target;
- reconnect reveals a different exact-unit identity;
- post-write validation failure.

Assert the application result matches existing safety semantics, never blindly retries an indeterminate state, never continues writes merely to reach post-validation after a terminal safety failure, and never reports `SUCCEEDED` unless full post-write validation passed.

## Required commands before v0.2.0 release

At minimum, all current project checks remain green:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python g502x.py selftest
python g502x.py --help
python g502x.py capabilities --json
```

The repository CI matrix must also pass on its existing Linux x64, Windows x64, and Windows x86 lanes. Any TUI-specific test dependency must be isolated so the core/CLI no-Textual path is also tested.

Before the v0.2.0 release is eligible, release/dependency gates must additionally prove:

- the core/CLI lane installs only the existing core lock and succeeds with Textual absent;
- every optional TUI dependency and transitive dependency is exactly pinned and hash-locked in a dedicated optional lock/equivalent reproducible input;
- a TUI lane installs only that locked optional set and runs the Textual harness tests;
- release manifest/SBOM metadata records the optional TUI dependency set shipped for v0.2.0 rather than describing only the core `hid` dependency;
- the deterministic release ZIP contains the TUI entry point/source and optional-install lock/metadata;
- an extracted release smoke succeeds both without optional dependencies (CLI/core path) and with the locked TUI dependencies installed;
- cross-platform release reproducibility still passes after the additional files/dependencies;
- version/tag/release gates identify v0.2.0, while the historical `v0.1.0` tag/release remains untouched;
- release notes are version-appropriate and do not reuse the current v0.1.0-only "First public release" wording.

## Hardware validation scope

No new physical hardware validation is required merely to add an adapter over unchanged operations. If implementation changes write ordering, transport behavior, recovery behavior, supported hardware, or persistent semantics, that exceeds this design and requires a separately approved milestone with corresponding evidence.
