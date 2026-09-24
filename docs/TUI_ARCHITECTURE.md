# Terminal UI architecture for v0.2.0

Status: design authority for the v0.2.0 TUI implementation. This document specifies architecture only; it does not change current CLI commands, hardware support, write semantics, requirements, or workflows.

## Goals

The v0.2.0 terminal UI provides a keyboard-first interactive adapter over the same operations that serve the CLI. CLI and TUI are adapters over one shared application/operations layer. It must make state and safety decisions visible without creating a second path to the hardware.

The architecture has five boundaries:

```text
CLI adapter ---------\
                      > application / operations ---- backend protocol ---- RealBackend ---- current device/HID stack
TUI adapter ---------/                              \
                                                     `-------------------- FakeBackend (tests)
```

The application/operations layer is the single orchestration authority for both adapters. Compilation, baseline checks, device identity, host guards, operation locking, backup creation, persistent-write sequencing, readback/reconciliation, and post-write validation must not be reimplemented in widgets or command handlers.

## Package boundaries

The intended v0.2.0 shape is:

```text
g502x_onboard/
  application/
    models.py          # typed operation/result/privacy contracts
    operations.py      # shared read-only and mutating use cases
    coordinator.py     # one-operation-at-a-time orchestration
    backend.py         # synchronous backend Protocol and RealBackend adapter
  tui/
    app.py             # Textual bootstrap, optional dependency boundary
    model.py           # explicit TUI state
    update.py          # events -> state + effects
    view.py            # state -> renderable view model/widgets
    effects.py         # worker scheduling; no hardware primitives
    screens/           # composition only
```

Exact filenames may change during implementation, but the dependency direction may not:

```text
tui -> application -> backend adapter -> existing device/HID stack
cli -> application -> backend adapter -> existing device/HID stack
```

`g502x_onboard/tui/**` must depend only on the public application facade and typed application models. It must not import `hid`, `libs.*`, HID++ transport modules, `g502x_onboard.device`, `application.backend`, or `RealBackend`. It also must not reach hardware by spawning `g502x.py`/the CLI, using `os.system`/`subprocess`, `runpy`, or dynamic-import escape hatches. Static architecture tests must enforce these boundaries.

Backend construction belongs to an application composition root/factory, not to TUI widgets, effects, screens, or the CLI adapter. The real backend is the only application-side bridge to current low-level device code. After CLI migration is complete, the CLI must use the same public application facade rather than retaining a second direct `.device` route.

## Shared application/operations layer

The shared layer exposes use cases, not HID primitives. Representative operations include:

- probe and compatibility inspection;
- setup/baseline administration;
- compile/plan/capacity;
- validate/status/inspect;
- shareable reports and private diagnostics;
- profile selection;
- backup creation;
- prepared apply/restore/baseline-restore operations.

The public contracts return typed data structures suitable for either CLI formatting or TUI rendering. They must not return live HID handles, raw transport objects, or widget types.

The existing CLI behavior is the compatibility reference. Moving orchestration into the shared layer must preserve current confirmations, safety gates, output privacy semantics, and persistent-write order unless a separate milestone explicitly changes them.

## Explicit Model / Update / View state

The TUI follows a Model/Update/View-inspired architecture. Safety-relevant state is explicit and inspectable rather than hidden in widget callbacks.

The model should contain, at minimum:

- current route/screen and focus intent;
- compatibility/read-only status;
- active foreground operation and phase;
- last completed result/error;
- prepared operation metadata when present;
- review acknowledgement and typed-confirmation input;
- privacy classification of displayed diagnostics;
- transient presentation state such as help or disclosure panels.

Events are explicit values: user intent, worker started, worker progress, worker completed, worker failed, revalidation invalidated, confirmation changed, and cancellation requested.

`update(model, event)` produces the next model plus zero or more effects. The view renders from the model. Widgets may emit events but may not independently decide that a write is authorized.

The design is "inspired" rather than requiring a pure functional framework: mutable Textual widget internals are acceptable, but hardware authority and safety phase transitions must be represented in the model/application contracts and testable without a terminal.

## Synchronous backend, asynchronous presentation

The hardware backend remains synchronous. HID++ calls, sector reads/writes, baseline access, reconciliation, and validation execute in ordinary blocking application methods.

The TUI invokes those methods in Textual workers (or an equivalent worker abstraction) so the event loop remains responsive. Workers may post typed progress/completion events back to the update layer. They must not expose a live device handle to the UI thread.

This separation prevents accidental concurrent HID access and keeps the proven synchronous write/reconciliation behavior intact.

## One hardware operation at a time

Only one operation that can touch hardware may be in flight per process. The TUI disables or rejects additional hardware actions while one is active.

That UI rule is only convenience. The shared application coordinator must also serialize hardware operations and must retain the existing OS-backed operation lock as the cross-process authority. A second CLI or TUI process therefore cannot bypass serialization.

Pure local rendering, help, config editing outside this project's write path, and viewing already-captured non-hardware data may continue while a worker is active, provided they cannot mutate the active operation.

## Operation classes

The application layer distinguishes:

- **read-only**: no persistent or volatile device mutation;
- **volatile mutation**: changes live state without persistent sector writes, such as profile selection;
- **persistent mutation**: may write the managed onboard domain.

Persistent mutations use the prepared-operation protocol in `TUI_SAFETY_MODEL.md`. Volatile mutations retain all existing identity, validation, host-guard, and confirmation requirements applicable to them.

## No background hardware polling

The TUI must not poll the mouse in the background. Hardware reads occur only because the user requested an operation/refresh or because an already-started foreground operation requires a read for its safety contract.

Timers may update presentation-only state (for example elapsed time or animations) but cannot call the backend. A visible status value may therefore be intentionally stale until the user refreshes it; the UI should label that state rather than silently polling.

## Optional Textual dependency

Textual is an optional UI dependency, not a requirement for the core CLI. Importing `g502x_onboard.cli`, running existing CLI commands, or running the core offline suite must not require Textual.

The future `tui` entry point imports Textual lazily. If the optional dependency is absent, it must fail before hardware access with a concise install instruction. The core `requirements.txt` remains sufficient for CLI/core use and must not acquire Textual merely because the TUI exists.

The exact optional-packaging filename is deferred, but the release properties are not: every optional TUI runtime dependency and transitive dependency must be exactly pinned and hash-locked in a dedicated optional lock/equivalent reproducible input; no release workflow may install a floating `textual` requirement. Release metadata/SBOM generation must include the optional TUI dependency set when it is part of the v0.2.0 distribution. The release archive must contain the TUI entry point/source and its reproducible optional-install metadata, and CI must smoke-test the extracted release both without Textual (core/CLI still works) and with the locked optional TUI dependencies installed.

## Terminal behavior

The TUI must remain usable in an 80x24 terminal:

- primary actions and safety state fit without horizontal scrolling;
- dense details collapse into scrollable secondary panes;
- destructive review and confirmation remain reachable at minimum size;
- truncation never removes a safety-critical label, operation type, read-only reason, or confirmation requirement.

The interface is keyboard-first. Every action, disclosure, review, confirmation, cancel-before-write action, and refresh action must be reachable without a mouse. Mouse support may be additive but cannot be required.

When `NO_COLOR` is present, semantic information cannot depend on color. Color styling is disabled or reduced while labels, symbols, text, focus indication, warnings, and success/failure states remain distinguishable. Terminal control sequences required to operate a full-screen TUI are not themselves considered semantic color.

## Error and progress model

Application operations return typed outcomes with a stable operation id and phase. The TUI may render progress, but progress is descriptive rather than authoritative.

Persistent mutation phases are defined in `TUI_SAFETY_MODEL.md`. Any error after write start must be presented as an operation result requiring reconciliation/validation context; the TUI must never convert an ambiguous transport exception into "not written" on its own.

## Explicit non-goals

v0.2.0 does not aim to:

- broaden persistent-write hardware, transport, firmware, or host support;
- add firmware, receiver firmware, DFU, or arbitrary HID++ writes;
- alter Profile 1 or sectors 6/7 recovery protection;
- create a daemon, tray process, resident companion, or background poller;
- make the HID backend asynchronous;
- replace the CLI or change existing command workflows merely for UI convenience;
- add force/unsafe overrides;
- make private diagnostics shareable;
- implement remote control, network APIs, plugins, scripting, or multi-device concurrency;
- modify the historical `v0.1.0` tag/release.
