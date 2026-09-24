# Terminal UI safety model for v0.2.0

Status: normative safety specification for the v0.2.0 TUI and shared application/operations layer. Existing `docs/SAFETY.md`, `SECURITY.md`, and `SUPPORTED_DEVICES.md` remain authoritative for the hardware boundary.

## Inherited invariants

The TUI does not create new write authority. All existing invariants continue to apply:

- Profile 1 / sector 1 remains protected recovery.
- Sectors 6 and 7 remain protected.
- Normal writes remain restricted to Sector 0, Profiles 2-5, and macro sectors 8-15.
- Firmware, receiver firmware, DFU state, and unrelated HID++ features are out of scope.
- Unknown devices, transports, firmware, or unstable unit identity remain read-only.
- Persistent writes require stable exact-unit identity and the matching active local baseline.
- G HUB and Logitech Onboard Memory Manager guards remain authoritative.
- Profile/recovery preconditions remain authoritative.
- Every persistent sector write is reconciled from fresh readback.
- Full post-write validation remains mandatory.
- No force/unsafe bypass exists.

A disabled button, hidden menu item, or TUI model flag is never sufficient authorization. The application/backend path must enforce every gate again.

## Safety principle: prepare, review, revalidate, execute

A destructive operation is never executed directly from a UI event. Persistent mutations use a prepared-operation protocol:

```text
user intent
  -> prepare (read-only)
  -> review
  -> typed confirmation
  -> revalidate under operation lock
  -> execute persistent transaction
  -> reconcile/read back
  -> post-write validate
  -> terminal result
```

The prepared object is a capability to request revalidation, not a capability to write.

## PreparedOperation contract

A `PreparedOperation` is immutable application data containing enough information to prove what the human reviewed and to detect staleness before execution. It should include:

- unique preparation id;
- operation kind (`apply`, `restore_backup`, `restore_baseline`, or future explicitly approved persistent operation);
- sanitized review model describing intended managed-domain changes;
- deterministic digest of the intended plan/target state;
- active-baseline fingerprint/digest binding required by the current operation;
- exact-unit identity binding in private application state, never exposed through a privacy-safe view;
- validated hardware/transport/firmware compatibility class;
- relevant observed preconditions, including recovery/profile state;
- host-guard result at prepare time;
- required typed-confirmation phrase;
- privacy classification for every attached diagnostic payload.

Prepared data must not contain a live HID handle. It is safe to retain while the human reviews because execution must revalidate it.

A preparation id is single-use once the application coordinator accepts an execution request and transitions that operation into `REVALIDATING`. The coordinator must atomically claim/consume that id before execution work can be duplicated. A concurrent or repeated submit for the same preparation must be rejected rather than queued as another transaction. After that claim, the same preparation id must not be accepted for a second execution attempt, regardless of whether revalidation refuses the operation, cooperative cancellation occurs before `WRITING`, or the transaction later succeeds or fails. Any later attempt requires a newly prepared, reviewed, and typed-confirmed operation.

For `apply`, the preparation must bind the exact normalized config/compiled plan and inherited baseline bytes represented to the user. Restore preparations must bind the exact backup/baseline target accepted by existing validation.

## Human review

The review screen presents the operation type, target, managed write domain, protected recovery domain, warnings, and a deterministic summary/digest sufficient to identify the prepared intent.

Review output must not leak private identity or baseline data. Private diagnostics may be shown only in an explicitly private diagnostic surface, never as part of a default shareable review.

The TUI cannot infer review from elapsed time, focus, scrolling, or opening a screen. A transition to confirmation is an explicit user action.

## Typed confirmation

Persistent destructive operations require an exact typed phrase. The phrase is supplied by the application contract and must preserve the semantic strength of the current CLI confirmation for the corresponding operation.

The current confirmation contract that the shared application/adapters must preserve is:

- apply: `APPLY CONFIG`;
- backup restore: `RESTORE BACKUP`;
- baseline restore: `RESTORE BASELINE`;
- enter programmable profile N: `ENTER PROFILE N`;
- return to the recovery profile: `BACK TO SAFE`.

Profile selection is a volatile mutation rather than a `PreparedOperation`, but its current typed confirmation remains mandatory. The TUI must not downgrade any of these phrases to a generic yes/no action.

Copy/paste may be permitted, but the text must match exactly after only the same normalization already accepted by the CLI (currently surrounding whitespace trimming). The TUI must not offer a one-click substitute, checkbox-only confirmation, force flag, or "remember my choice" option.

Confirmation does not authorize a stale prepared operation. It only requests execution-time revalidation.

## Revalidation after review

After typed confirmation and before any persistent write, the application coordinator acquires the existing cross-process operation lock and revalidates all write authority from fresh state.

At minimum it must verify:

- the active local baseline still matches the prepared baseline binding;
- the connected device is the exact stable unit and still satisfies the validated compatibility/write policy;
- transport/firmware/device-class write eligibility is unchanged;
- G HUB and Logitech Onboard Memory Manager guards still pass;
- required SAFE/recovery state still passes;
- the prepared plan/target digest is unchanged;
- any operation-specific backup/target validation still passes;
- the intended write domain contains no protected sector.

Where an existing operation already performs stronger checks, those checks remain authoritative.

If any value differs, execution is refused before the first persistent write. The prepared operation becomes invalid and the user must prepare and review a new operation. The TUI must not offer "continue anyway".

## Persistent transaction phases

The application exposes explicit phases for persistent mutation:

```text
PREPARING
PREPARED
REVIEWING
CONFIRMING
REVALIDATING
ARMED
WRITING
RECONCILING
POST_VALIDATING
SUCCEEDED | FAILED
```

`ARMED` means all execution-time gates passed but no persistent write has started. Cancellation is still allowed at this point.

The transition to `WRITING` occurs immediately before the first backend action that can persist bytes. From that transition onward, the transaction is non-cancellable through the application's cooperative/TUI cancellation channel.

## Non-cancellable after first persistent write begins

Here, "cancellation" means a cooperative application/TUI request such as Esc, back, modal close, or worker-cancel. It does **not** mean swallowing process-level interruption. Existing CLI `KeyboardInterrupt`/exit behavior remains a compatibility constraint unless a separately approved milestone changes it; SIGINT/SIGTERM, terminal loss, process kill, power loss, OS termination, or machine failure may still interrupt the process.

Before `WRITING`, cooperative Esc/back/cancel may abandon the prepared operation without hardware mutation.

Once `WRITING` begins:

- the TUI must not translate Esc/back/modal close/worker-cancel into thread termination, process termination, or cancellation of the active hardware call;
- the TUI must not replace the active operation or start another hardware operation;
- closing or navigating away from a modal cannot stop the worker;
- a cooperative worker cancellation request is ignored/deferred until the transaction reaches a terminal result;
- while the process remains alive and no independent terminal safety/backend failure occurs, the application continues the write/reconciliation/validation path that the operation would have followed without the cancellation request;
- cooperative cancellation itself must never skip required reconciliation or a success-path post-write validation;
- an independent terminal failure (for example indeterminate bytes, identity hot-swap, guard failure, disconnect, or another fail-closed condition) still ends the transaction according to existing semantics; the application must not perform extra writes merely to "finish" after such a failure;
- the UI must visibly state that the transaction is in a non-cancellable safety phase.

This is an in-process cooperative guarantee, not a claim that Python or the operating system can make a hardware transaction uninterruptible. Process-level interruption and hardware disconnect remain governed by existing reconciliation/recovery semantics; the TUI must not invent an automatic unsafe recovery write or report that no write occurred without authoritative readback. A persistent operation may reach `FAILED` from a write/reconciliation phase; it does not have to execute further writes or reach `POST_VALIDATING` after an independent terminal safety failure.

## Ambiguous write outcomes

Transport acknowledgements are not authoritative. If a write call fails after bytes may have committed, the application/backend follows existing fresh-readback reconciliation and exact-unit reconnect logic.

The TUI may display "reconciling" or an indeterminate state while this occurs, but it cannot classify the write as failed/unchanged until the application has done so from observed bytes.

No blind retry is permitted when observed bytes match neither the previous state nor the intended target.

## Post-write validation is a success gate

Full post-write validation remains mandatory before a completed persistent operation may report `SUCCEEDED`. A cooperative cancellation request after `WRITING` cannot bypass that validation on an otherwise successful path.

This does not authorize continuing writes after an independent terminal failure. If existing write/reconciliation logic terminates because bytes are partial/indeterminate, exact-unit identity changes, a guard fails closed, or another terminal safety condition occurs, the operation remains `FAILED`; its result must preserve the authoritative reconciliation state and explicitly record whether full post-write validation completed or was not safely reachable. The TUI must not promote such a result to success.

## One hardware operation at a time

Only one hardware operation may execute within the process, and the existing OS-backed operation lock remains the cross-process authority.

The TUI must reject a second hardware action while one is active. The application coordinator must independently enforce the same invariant so adapter bugs cannot create concurrent device access.

## No background hardware polling

No worker, timer, reactive callback, screen mount handler, animation, or idle task may read hardware merely to keep the screen fresh.

Hardware access is allowed only for:

- an explicit user-requested read/refresh;
- preparation of an explicitly requested operation;
- revalidation/execution/reconciliation/post-validation of the active foreground operation.

A stale screen is safer than hidden polling. The TUI should show when state was last refreshed.

## Unknown hardware is read-only

Unknown device, transport, firmware, memory geometry, or unstable unit identity may be probed only through existing read policies. Persistent operation preparation must return a typed read-only refusal with the evidence/reason; it must not produce an executable `PreparedOperation`.

The TUI should disable or omit persistent actions for such a target, but application/backend refusal remains mandatory even if the UI is bypassed.

Read-only discovery never upgrades support. Persistent support continues to require repository-approved hardware evidence.

## Privacy classes

TUI/application outputs are explicitly classified into three classes so ordinary user-authored configuration is not confused with either public evidence or raw diagnostics:

### Privacy-safe / shareable

Derived only from existing sanitized reporting/public-summary logic. These views may include compatibility and structural summaries that intentionally omit unit IDs, serial numbers, private fingerprints, local baselines/backups, raw sectors, local paths, and personal macro/profile contents.

Any export offered as shareable must pass the existing privacy validator (`assert_public_report_safe` or its shared-layer equivalent) on the exact emitted payload.

### Local sensitive / user-authored

Contains data the user intentionally supplied or requested for the local workflow, such as configuration paths, profile names, bindings/macros, deterministic plan/review detail, or backup labels. The current CLI already treats plan JSON as local configuration output rather than a privacy-minimized shareable report.

A prepared-operation review normally belongs to this class: it may show enough user-authored intent to review the operation, but it must not expose exact-unit identifiers, private fingerprints, raw sector/baseline bytes, or other private diagnostic state. Local-sensitive content:

- may be displayed as part of the explicit local operation without a diagnostic opt-in;
- must never be relabeled or exported through a shareable-report path;
- must not be placed in operation ids, worker names, telemetry, or default crash titles/log labels;
- must be cleared when transitioning to a privacy-safe/shareable view rather than merely hidden.

### Private diagnostic

May contain per-unit identity, serials, private fingerprints, raw sectors/state, baseline/backup contents, or other device-specific material already treated as private by the project.

Private diagnostic surfaces must:

- be labeled `PRIVATE` before reveal/export;
- never be copied to a shareable report implicitly;
- default to private-state output locations where current commands do so;
- avoid putting secrets/private identifiers into worker names, operation ids, logs, crash titles, or telemetry;
- require explicit opt-in for raw/private detail, preserving current CLI semantics.

The classification is part of typed application data so a widget cannot accidentally present local-sensitive or private-diagnostic data as shareable.

Errors are typed outputs under the same privacy model. Error detail may contain local-sensitive or private-diagnostic material only when it carries that classification and is rendered solely in the corresponding local/private surface. Default/privacy-safe error summaries and default shareable logs must be sanitized; raw exception strings must never be implicitly promoted to privacy-safe/shareable output or reused as operation ids, worker names, crash titles, or telemetry fields.

## Host guards and read-only presentation

A host-guard failure is a write refusal, not a warning that can be dismissed. If guard verification itself fails closed on a platform where it is authoritative, the TUI presents the failure and does not prepare a persistent operation.

The TUI may still show already-available local data and explicitly permitted read-only information if doing so does not violate existing command semantics.

## Recovery boundary

Profile 1 and sectors 6/7 are never rewritten by ordinary TUI configuration. Restore/baseline-restore operations remain limited to the existing programmable domain and preserve current staging/readback/recovery validation order.

The TUI does not add a recovery wizard that writes protected sectors, firmware, DFU, or guessed factory state.
