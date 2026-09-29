# TUI UX Contract & Information Architecture — Phase 7 / P7.1

Status: normative product/UX design authority for Phase 7 implementation.
Baseline: 2db2ef68db0cea71f5e8fddc20e101b6d35ad523.
Scope: design/docs only. This document does not authorize runtime, application, backend, device, persistent-protocol, test, release-tooling, tag, release, or hardware changes.

## 1. Purpose

Phase 7 changes the terminal UI from an engineering-oriented presentation into a task-oriented product UI while preserving the existing safety architecture and application authority.

The product goal is:

    safe engineering TUI -> intuitive product TUI

This is not a cosmetic redesign. The contract is about information architecture, task discoverability, copy, navigation, progressive disclosure, contextual help, first-run comprehension, terminal responsiveness, and accurate communication of safety state.

A user who does not know internal lifecycle enums must still be able to determine:

1. whether a supported mouse is available;
2. whether physical state has actually been read in this session;
3. whether the current target is usable, read-only, or blocked for the requested task;
4. what the relevant current profile/state is when it has been read;
5. whether any operation is pending;
6. whether persistent writing has started;
7. whether a completed write was reconciled and post-validated;
8. what the next safe action is;
9. how to go back, refresh, get help, or exit.

Technical truth remains available. It stops being the default organizing principle.

## 2. Frozen authority and safety boundary

This UX contract MUST NOT change or imply a change to any of the following authorities:

- ApplicationFacade remains the only TUI application authority.
- The TUI does not import or call HID/device/backend primitives directly.
- The TUI does not construct RealBackend.
- The TUI does not spawn the CLI, use runpy, dynamic imports, subprocess escape hatches, or any equivalent second hardware path.
- Hardware reads remain explicit foreground operations; there is no background hardware polling.
- The existing operation coordinator and OS-backed operation lock remain authoritative.
- Persistent operations remain prepare/review/exact-confirmation requests followed by execution-time revalidation.
- PreparedOperation remains a capability to request revalidation, not a capability to write.
- Profile 1 / sector 1 remains protected recovery state.
- Sectors 6 and 7 remain protected.
- Persistent writes remain restricted to the existing managed domain.
- Current device/transport/firmware support remains unchanged.
- Current host guards remain authoritative and fail closed where specified.
- Reconciliation from fresh readback remains authoritative after writes.
- Full post-write validation remains a success gate.
- WRITING and later phases remain non-cancellable through cooperative TUI/application cancellation.
- Privacy classes and privacy downgrade behavior remain authoritative.
- Release-verifier architecture remains unchanged.

If an implementation of this UX appears to require changing one of those authorities, the implementation MUST stop and request a new orchestration decision. The UX specification is not implicit authorization to change application or hardware behavior.

## 3. Current-state grounding

The current TUI exposes these existing capabilities through the public application facade:

| Existing TUI action | Existing application operation | Mutation class | Phase-7 product placement |
| --- | --- | --- | --- |
| Probe | probe() | read-only | Diagnostics |
| Refresh/status | status(private=False) | read-only | Contextual Home refresh; Diagnostics detail |
| Validate | validate() | read-only | Diagnostics |
| Plan | plan(config_path) | local/read-only with respect to hardware | Configuration |
| Prepare apply | prepare_apply(config_path) | preparation is read-only; execution may persist | Configuration |
| Restore backup | prepare_restore_backup(backup_path) | preparation is read-only; execution may persist | Backup & Restore |
| Restore baseline | prepare_restore_baseline() | preparation is read-only; execution may persist | Backup & Restore |
| Switch profile | switch_profile(target, confirmation) | volatile mutation | Configuration |
| Public report | report_probe(...) | read-only | Diagnostics |
| Privacy surface | local presentation state | presentation only | Diagnostics / disclosure |
| Help | local presentation state | presentation only | Contextual Help |

Phase 7 MUST NOT invent a new persistent operation to fill a visual design.

The current TUI state engine already represents:

- route and focus intent;
- compatibility-derived read-only state;
- one foreground operation;
- persistent kind and lifecycle phase;
- prepared-operation review data;
- explicit review acknowledgement;
- exact confirmation input;
- cancellation availability;
- WRITING+ non-cancellable truth;
- worker-fault unresolved truth;
- terminal success/failure facts;
- writing_started;
- reconciliation_completed;
- post_validation_completed;
- privacy surface and classified payloads;
- help and technical disclosure state.

The current presentation problem is that implementation-oriented facts such as internal phase names, target digest, architecture/transport, host guard state, and raw terminal fact booleans dominate the foreground. Phase 7 preserves those facts but changes their hierarchy.

### 3.1 Typed-state requirement

Home requires product-facing retained facts such as “state has not been read yet”, “last explicit read succeeded”, active profile, and a human-readable device/readiness summary.

The existing application already returns typed ProbeSnapshot and StatusSnapshot data for the underlying facts. Phase 7 MUST NOT reconstruct those facts by parsing the current runner's human-formatted completion string.

A later implementation may add presentation-only typed fields/events so already-existing typed application data survives into the TUI model. Such a change does not grant new hardware authority.

If P7.2/P7.3 cannot satisfy Home from existing typed application results without changing application/backend contracts, it MUST stop and document the conflict rather than fabricate state.

## 4. Product principles

### 4.1 Task first

The top-level interface is organized around user goals, not internal implementation phases.

Users choose tasks such as:

- view current device state;
- configure profiles;
- review a configuration plan;
- apply a configuration;
- restore from a backup;
- restore the active validated baseline;
- switch the active profile;
- run diagnostics;
- generate a public report.

Internal enums are not top-level destinations.

### 4.2 Safety translated, not hidden

The product layer translates safety state into plain language while retaining exact technical state on demand.

The default UI MUST make these distinctions explicit:

- not read yet;
- reading;
- read successfully;
- read failed;
- read-only;
- blocked/refused;
- prepared but not written;
- cancellable execution stage;
- writing/verification in progress and non-cancellable;
- completed and verified;
- failed before write;
- failed after write;
- unresolved hardware outcome.

Friendly wording MUST NOT erase precise meaning.

### 4.3 Progressive disclosure

Technical details remain accessible from the context where they matter. They are not mixed into the primary task narrative unless the user opens Technical details.

Technical disclosure MUST remain privacy-class aware.

### 4.4 Keyboard first

Every essential flow remains fully usable without a mouse.

Mouse support may be additive but MUST NOT be required for:

- reading current state;
- navigation;
- plan;
- preparation;
- review;
- exact confirmation;
- cancellation before WRITING when allowed;
- result inspection;
- technical disclosure;
- help;
- refresh;
- exit.

### 4.5 NO_COLOR parity

Color can reinforce state but can never be the sole carrier of state.

Every meaningful state has a text label and ASCII-safe semantic marker.

### 4.6 80x24 is the supported baseline

80 columns by 24 rows is the minimum supported product layout.

At 80x24 the essential task, safety state, exact confirmation, and result truth MUST remain reachable without horizontal scrolling.

Larger terminals may enrich layout. Smaller terminals enter a constrained safety mode and MUST NOT silently squeeze or truncate safety-critical controls.

## 5. Target information architecture

The conceptual information architecture has five areas:

1. Home
2. Configuration
3. Backup & Restore
4. Diagnostics
5. Contextual Help

This is an information architecture, not a required class hierarchy. P7.2/P7.3 may implement these as views, panels, routes, modes, or composed widgets provided the behavior matches this contract.

Refresh is always a contextual action, never a destination.

### 5.1 Home

Purpose:

- establish whether physical state has been read;
- summarize the latest authoritative user-visible state;
- show readiness/read-only/blocking state without overstating write authority;
- show whether an operation is active;
- show whether any persistent write has started;
- present the next task choices;
- keep Refresh, Help, and Exit obvious.

Home MUST NOT equate cached/local presentation with a fresh physical read.

### 5.2 Configuration

Contains existing configuration-oriented tasks:

- view/plan a config file;
- prepare/apply a config file;
- switch active profile using the existing exact confirmation contract.

Configuration MAY show last-read Home context in a compact status line, but it MUST NOT silently refresh hardware when entered.

### 5.3 Backup & Restore

Contains only existing restore capabilities currently exposed by the TUI:

- Restore backup;
- Restore baseline.

Phase 7 does not add a new manual backup write workflow merely because the area is named “Backup & Restore”. Existing automatic/private safety-backup semantics remain application authority.

### 5.4 Diagnostics

Contains existing read-only/diagnostic tasks and disclosure:

- Probe;
- Validate;
- Public report;
- latest shareable status detail;
- Technical details;
- privacy-class switching/reveal only where existing authority permits.

Private diagnostic data MUST remain explicitly labeled PRIVATE before reveal.

### 5.5 Contextual Help

Help is an overlay/panel bound to the current context, not one giant manual.

Opening, navigating, or closing help MUST perform zero facade/backend calls.

## 6. Home contract

### 6.1 Truth states

Home MUST model the physical-read truth separately from generic screen lifetime.

The minimum presentation states are:

- NEVER_READ: no successful physical state read has completed in this TUI session;
- READING: an explicit foreground read is active;
- READ_OK: the latest explicit read completed successfully;
- READ_FAILED: the latest explicit read failed or was refused;
- ACTIVE_OPERATION: another foreground operation is active and owns hardware-operation precedence.

These are presentation concepts. They MUST be derived from existing typed operation events/results and MUST NOT become write authority.

A prior READ_OK snapshot can become stale in the real world because the product intentionally does not poll. Therefore Home wording MUST describe it as the result of the last explicit read, not as continuously live truth.

### 6.2 Meaning of “Ready”

“Ready” in the product layer means only:

- a supported/accepted state read completed successfully; and
- no current presentation-level read-only/refusal condition is known for the displayed task context.

“Ready” MUST NOT mean that a persistent write has already passed execution-time gates.

Persistent write authority is established only by the existing prepare/revalidation/application path.

### 6.3 Initial Home — no physical read yet

Exact primary wording:

~~~text
G502 X Onboard

[ ] Device state not read yet

Read the current onboard state before making decisions from this screen.

> Read current onboard state

  Configuration
  Backup & Restore
  Diagnostics

Reading state does not modify the mouse.

R Read state   ? Help   Q Exit
~~~

Rules:

- The first safe action is Read current onboard state.
- No supported-device checkmark may appear before a physical read.
- Cached local paths, baseline metadata, or a previous process's data MUST NOT be styled as current physical truth.
- Configuration/restore areas may be navigable for local inspection, but write preparation actions MUST continue to rely on application checks; the UI MUST NOT imply readiness.
- No automatic read occurs on mount.

### 6.4 Ready/refreshed Home

Exact primary wording pattern:

~~~text
G502 X Onboard

[OK] G502 X LIGHTSPEED
[OK] State read successfully
[OK] Device available for supported tasks

Current profile     Profile 1 — SAFE
Onboard state       Validated

What would you like to do?

> View current configuration
  Configure profiles
  Backup & Restore
  Diagnostics

No operation is pending.

Up/Down Navigate   Enter Select   R Refresh   ? Help   Q Exit
~~~

Rules:

- Exact device naming is shown only when present in existing typed read results. Otherwise use “Supported device available”; never invent a model name.
- “State read successfully” means the last explicit foreground read succeeded.
- “Validated” is shown only when the existing result actually establishes validation; otherwise use a narrower phrase such as “State read”.
- “Profile 1 — SAFE” is shown only when active_profile=1 and existing project terminology supports SAFE. Other profiles use “Profile N”.
- Ordinary refreshed idle Home MUST NOT show operation-scoped write facts when no operation exists.
- “Nothing has been written yet” or equivalent operation-scoped wording is reserved for a real prepared/active operation context and only when authoritative lifecycle state establishes that no persistent write has started.
- The Home snapshot is not automatically refreshed after time passes.

### 6.5 Read-only Home

Primary pattern:

~~~text
[!] Device state read
[!] This device is read-only

You can inspect supported state, but persistent changes are disabled.

Current profile     Profile N
Next safe action    Open Diagnostics or refresh state

R Refresh   ? Help   Q Exit
~~~

The reason is shown in plain language when it is privacy-safe. Technical eligibility details remain secondary.

### 6.6 Failed-read Home

Primary pattern:

~~~text
[X] Device state could not be read

The application could not obtain a usable device state from this read.

Check the connection and try again.
Open Help or Diagnostics for any additional information that is available
to the current privacy surface.

R Try again   ? Help   Q Exit
~~~

READ_FAILED means only that the latest explicit read failed or was refused. It does not by itself mean no device, unsupported device, competing Logitech writer, transport failure, firmware failure, or any other more specific cause.

Condition-specific product copy MAY be used only when an existing typed result available to the current privacy surface independently identifies that condition. The UI MUST NOT infer a public cause from raw exception text, PRIVATE diagnostic detail, or humanized runner strings. If no privacy-safe typed distinction exists, the cause-neutral failed-read copy above is the required fallback.

## 7. Primary vs technical information hierarchy

The classifications below are grounded in the current ViewModel, PreparedOperation, and typed application results.

| Current field/concept | Classification | Default presentation | Rule |
| --- | --- | --- | --- |
| read_only | Primary | Home/task status | Render as “This device is read-only” or equivalent. |
| read_only_reason | Primary when privacy-safe; otherwise governed | Inline reason | Never reveal disallowed detail. |
| operation_active | Primary | Global task status | User must know another foreground operation owns the interaction. |
| operation_action | Primary after humanization | Task title | Show “Reading device state”, “Applying configuration”, etc., not enum value. |
| persistent_kind | Primary after humanization | Review/operation title | Apply configuration / Restore backup / Restore baseline. |
| phase | Secondary as raw enum; Primary as translated step | Operation view | Human step is foreground; exact enum appears in Technical details. |
| review_visible | Structural | Review route | Not itself shown as text. |
| prepared.review | Primary LOCAL SENSITIVE within explicit workflow | Review summary | Show task-relevant changes. Never relabel as shareable. |
| review_acknowledged | Primary control state | Review | Explicit acknowledgement remains required by TUI flow. |
| required_confirmation_phrase | Primary | Confirmation | Exact phrase remains visible and unchanged. |
| confirmation_input | Primary LOCAL SENSITIVE interaction state | Confirmation | Cleared according to existing lifecycle/privacy rules. |
| confirmation_matches | Structural/Primary control affordance | Enables submit | Exact match remains required. |
| cancellation_available | Primary | Active operation/footer | Must be explicit. |
| non_cancellable | Primary | WRITING+ banner | Must be unmistakable and non-color-dependent. |
| worker_fault_unresolved | Primary critical | Active operation | Must say hardware outcome is unknown; do not show terminal success/failure. |
| safety_label | Primary | Banner | Humanize without weakening meaning. |
| terminal_outcome | Primary | Result heading | Success / Failure. |
| writing_started | Primary on persistent result | Result facts | Answers whether any persistent write began. |
| reconciliation_completed | Primary on persistent result | Result facts | Answers whether fresh-readback reconciliation completed. |
| post_validation_completed | Primary on persistent result | Result facts | Answers whether final validation completed. |
| terminal error_code | Secondary | Technical details / support context | Primary copy should explain consequence first. |
| message | Primary only when allowed by privacy class | Task/result body | Must retain classification. |
| detail | Hidden / privacy-governed | PRIVATE diagnostics only | Never ordinary/shareable. |
| privacy_label | Secondary normally; Primary when revealing sensitive surface | Disclosure header | PRIVATE must be visible before private detail. |
| config_path_input | Primary LOCAL SENSITIVE within Configuration | Task form | Never shown on SHAREABLE surface. |
| backup_path_input | Primary LOCAL SENSITIVE within Restore backup | Task form | Never shown on SHAREABLE surface. |
| profile_target_input | Primary within profile task | Task form | Valid targets remain 1..5. |
| profile_confirmation_input | Primary interaction state | Profile task | Exact existing phrase remains required. |
| prepared.target_digest | Secondary technical | Technical details | Do not dominate review. |
| compatibility.architecture | Secondary technical | Technical details | Exact value remains available. |
| compatibility.transport | Secondary technical | Technical details | Exact value remains available. |
| compatibility.write_allowed / eligibility | Primary as “read-only/eligible”; exact value Secondary | Status + details | Human state foreground, exact policy value secondary. |
| host_guard_clear | Primary only when blocking; exact boolean Secondary | Safety check / details | A host guard failure is a refusal, not dismissible warning. |
| observed_preconditions | Secondary unless one is blocking | Technical details or targeted error | Do not dump implementation vocabulary on Home. |
| operation_id | Secondary technical | Technical details/support | Opaque token only; never include private content. |
| route/focus intent | Hidden structural | Not displayed as product data | Internal presentation state. |

### 7.1 Hidden/privacy-governed information

The following MUST NOT appear on ordinary shareable surfaces unless existing privacy authority explicitly permits it:

- exact-unit identifiers;
- serials;
- private fingerprints;
- raw sectors;
- baseline/backup contents;
- local private paths;
- personal macro/profile contents when the surface is shareable;
- raw private exception detail;
- private diagnostic payloads.

Local-sensitive prepared review data can appear in the explicit local workflow, but it is not shareable.

A transition to SHAREABLE MUST clear disallowed sensitive state according to the existing model; hiding a widget is not sufficient.

## 8. Navigation contract

### 8.1 Global keyboard behavior

| Key | Default behavior | Disabled/overridden when |
| --- | --- | --- |
| Up / Down | Move selection within the current task/menu/list. | A text input owns cursor/editing semantics; no hidden hardware action is ever triggered. |
| Tab | Move focus forward through visible enabled controls. | Never focuses hidden/disabled controls. |
| Shift+Tab | Move focus backward through visible enabled controls. | Same constraints as Tab. |
| Enter | Activate selected/focused action; submit an exact confirmation only when all confirmation gates are satisfied. | Disabled controls and incomplete confirmations do nothing. |
| Esc | Close Help first; otherwise back/abandon/cancel according to operation state. | At WRITING+ it cannot cancel or navigate away from authoritative operation truth. |
| R | Explicit refresh/read current state. | Disabled during another foreground hardware operation, below minimum layout, and while printable input focus has precedence. |
| ? | Open context-sensitive Help. | When a text input owns printable characters, “?” is input text; Help remains reachable by Tab/focus. |
| Q | Safe exit intent. | Never terminates a WRITING+ transaction through cooperative UI logic. Printable input focus consumes “q”. |

Global shortcuts MUST NOT be hardware authority. They only create typed user-intent events routed through existing application authority.

### 8.2 Focused-input precedence

Printable keys belong to the focused Input before route/global letter shortcuts.

This applies to:

- R/r;
- Q/q;
- ?;
- any route accelerator such as A/B/D/G/N/P/S/V.

Esc remains a priority safety/navigation key as in the current architecture.

Enter inside an input follows that input's documented submit behavior. It MUST NOT trigger an unrelated global action.

If Help is needed while typing, the user can:

1. Tab to the visible Help control and press Enter; or
2. Esc according to the current safe context, then use ? when no printable input owns focus.

No implementation may solve shortcut collisions by stealing normal text characters from an input.

### 8.3 Home accelerators

Home MAY expose these route accelerators when no text input owns focus:

- C — Configuration
- B — Backup & Restore
- D — Diagnostics
- R — Refresh/read state
- ? — Help
- Q — Exit

Up/Down + Enter remains the canonical discoverable path; letter accelerators are additive.

### 8.4 Configuration accelerators

Within Configuration, existing operation accelerators may remain:

- N — Plan configuration
- A — Apply configuration
- S — Switch profile
- R — Refresh current state when no operation is active
- T — Technical details, if implemented as an accelerator
- ? — Help
- Esc — Back / cooperative cancel according to state

The exact visible labels MUST make the action discoverable without knowing the shortcut.

### 8.5 Backup & Restore accelerators

- B — Restore backup
- L — Restore baseline
- R — Refresh when allowed
- T — Technical details
- ? — Help
- Esc — Back / cooperative cancel according to state

### 8.6 Diagnostics accelerators

- P — Probe
- V — Validate
- G — Public report
- R — Refresh/status
- T — Technical details
- ? — Help
- Esc — Back

Diagnostics navigation does not authorize private diagnostic reveal. Privacy-class rules remain separate.

### 8.7 Active persistent-operation precedence

Once persistent execution has been requested:

- a second hardware action is unavailable;
- Refresh is unavailable;
- route-changing actions cannot replace the active operation;
- Help remains local and available;
- technical disclosure may remain available if privacy permits and it performs zero hardware calls;
- pre-WRITING cooperative cancellation is available only when application state says it is;
- from WRITING onward, Esc/Q/cancel controls MUST NOT fabricate cancellation, kill the worker, or leave the user with a false “cancelled” state.

### 8.8 Below-minimum precedence

Below 80x24:

- no new hardware operation may be started;
- no persistent confirmation may be newly submitted;
- hidden primary controls must not remain shortcut-active;
- Help and safe exit remain available when there is no active non-terminal hardware operation;
- an already-active operation remains authoritative and must not be discarded because of resize;
- before WRITING, an existing cooperative cancel/back intent may remain reachable where current authority allows it;
- during WRITING+, the constrained view must preserve “operation still running / cancellation unavailable” truth and must not exit the process through the ordinary Q path.

## 9. Contextual Help contract

The primary Help key is ? outside focused printable text input.

Every Help surface MUST answer:

- What is this screen?
- What is safe to do here?
- What keys/actions are currently available?
- Has anything been written yet?
- Is cancellation available?

Help MUST describe current state, not generic hypothetical state only.

### 9.1 Home Help

Must explain:

- Home shows the last explicit read, not live polling.
- R performs an explicit read and does not itself modify onboard state.
- How to enter Configuration, Backup & Restore, and Diagnostics.
- Whether a physical read has completed in this session.
- Whether any operation is active.
- How to exit.

### 9.2 Configuration Help

Must explain:

- Plan is local/read-only with respect to hardware.
- Apply uses Prepare -> Review -> Confirm -> Apply -> Verify.
- Profile switch is the currently exposed volatile profile operation and uses exact confirmation.
- Paths and user-authored configuration are LOCAL SENSITIVE.
- Whether current state has been read.
- Which actions are disabled because of read-only/busy/constrained state.

### 9.3 Review Help

Must say:

- This screen is a review of an immutable prepared intent.
- Nothing has been written by preparation/review.
- The review shows what may change and what remains protected.
- Acknowledging review is explicit.
- Esc cancels/abandons before write where allowed.
- Technical details include digest/compatibility/preconditions.
- Continuing leads to exact typed confirmation, not directly to an unreviewed write.

### 9.4 Confirmation Help

Must say:

- Nothing has been written yet.
- The exact phrase is supplied by the application contract.
- Only surrounding-whitespace normalization already allowed by the application is accepted.
- Enter submits only when the exact phrase matches and review was acknowledged.
- Confirmation requests execution-time revalidation; it does not bypass stale-state checks.
- Esc requests/abandons before write when allowed.

### 9.5 Active operation Help

Must dynamically say:

Before WRITING:
- The application is checking or arming the reviewed operation.
- Cancellation is available only if current application state says so.
- No claim of writing is made until writing_started/WRITING authority exists.

WRITING+:
- Persistent writing or verification has begun.
- Cooperative cancellation is unavailable.
- Esc/Q do not stop the active transaction.
- Reconciliation/post-validation must determine the authoritative outcome.
- Do not retry while outcome is unresolved.

### 9.6 Diagnostics Help

Must explain:

- Probe/Validate/Status/Public report are read-oriented diagnostic tasks currently exposed by the TUI.
- Public report is privacy-safe only through the existing sanitized report path.
- LOCAL SENSITIVE and PRIVATE are not shareable.
- Technical details are subordinate to task state.
- Opening diagnostics/help performs no hidden refresh.

## 10. Copy system

The UI has two copy layers.

### 10.1 Primary language

Primary copy is human-facing, task-oriented, and consequence-first.

Canonical phrases:

- Device state not read yet
- Reading current onboard state
- State read successfully
- Device ready
- This device is read-only
- Device state could not be read
- No changes are pending
- Nothing has been written yet
- Checking device and safety conditions
- Ready to write
- Writing changes to onboard memory
- Verifying written state
- Cancellation is no longer available
- Changes applied and verified
- Operation stopped before writing
- Write started; final state was not fully verified
- Hardware outcome is unknown
- Condition-specific when existing privacy-safe typed authority identifies a competing Logitech writer: Close G HUB and Logitech Onboard Memory Manager before continuing
- State changed since review; prepare again

### 10.2 Technical language

Technical disclosure may retain exact concepts such as:

- PREPARING
- PREPARED
- REVIEWING
- CONFIRMING
- REVALIDATING
- ARMED
- WRITING
- RECONCILING
- POST_VALIDATING
- target digest
- plan digest
- architecture
- transport
- write eligibility
- host guard
- operation id
- error code
- writing_started
- reconciliation_completed
- post_validation_completed

### 10.3 Copy rules

1. Human consequence comes before implementation term.
2. Raw enum names are never the only explanation.
3. Never say “safe” when the intended meaning is merely “last read succeeded”.
4. “Ready” never replaces application write revalidation.
5. “Nothing has been written yet” is permitted only before authoritative write start.
6. “Nothing was written” on a failure is permitted only when authoritative result/state says writing_started=false or the failure is known to have occurred before the persistent write boundary.
7. After WRITING begins, never imply “cancelled”, “unchanged”, or “not written” without authoritative reconciliation/result.
8. “Changes applied and verified” requires all of:
   - success=true;
   - terminal phase SUCCEEDED;
   - writing_started=true;
   - reconciliation_completed=true;
   - post_validation_completed=true.
9. A worker/adapter fault during WRITING+ with no authoritative terminal result is “Hardware outcome is unknown”, not “Failed”.
10. Host-guard failure is a blocking refusal, not a dismissible warning.
11. Privacy labels are literal and stable:
    - SHAREABLE
    - LOCAL SENSITIVE
    - PRIVATE
12. Technical/private detail cannot be promoted into primary shareable copy.
13. Do not fabricate progress percentages. Use named stages unless deterministic completed/total units are supplied by the application.
14. Terminal outcome is not a lifecycle fact bundle. A terminal FAILURE does not by itself determine whether writing started, reconciliation completed, or post-validation completed. Product copy MUST derive each of those facts independently from authoritative terminal state.

## 11. Persistent-flow UX

The user-facing journey is:

    Prepare -> Review -> Confirm -> Apply -> Verify

The underlying application protocol remains unchanged.

### 11.1 Mapping to internal lifecycle

| User-facing step | Existing internal state | Primary wording | Write truth | Cancellation truth |
| --- | --- | --- | --- | --- |
| Prepare | PREPARING | Preparing a review | Nothing has been written yet | Available only according to current application/TUI authority |
| Review | PREPARED / REVIEWING | Review what will change | Nothing has been written yet | Available |
| Confirm | CONFIRMING | Type the exact confirmation | Nothing has been written yet | Available |
| Apply — safety check | REVALIDATING | Checking device and safety conditions | Nothing has been written yet | Application snapshot decides |
| Apply — armed | ARMED | Ready to write | Nothing has been written yet | Application snapshot decides; current contract allows until write boundary |
| Apply — write | WRITING | Writing changes to onboard memory | Writing has started | Cooperative cancellation unavailable |
| Verify — reconcile | RECONCILING | Reading back written state | Writing has started | Cooperative cancellation unavailable |
| Verify — post-validation | POST_VALIDATING | Verifying final onboard state | Writing has started | Cooperative cancellation unavailable |
| Complete | SUCCEEDED | Changes applied and verified | Writing occurred | Terminal |
| Complete | FAILED | Humanized failure based on authoritative result | Must show writing_started truth | Terminal unless adapter fault remains unresolved |

### 11.2 Before writing

The user MUST see:

- operation type;
- source/target meaningful to the task;
- what will change;
- what remains protected;
- warnings;
- that nothing has been written yet;
- whether known preconditions are acceptable;
- required exact confirmation phrase before submission.

Target digest, architecture, transport, and exact internal preconditions are available under Technical details.

### 11.3 During pre-write execution

REVALIDATING and ARMED MUST communicate:

- the reviewed operation is being rechecked against fresh execution-time authority;
- no persistent write has started yet;
- cancellation is available only if the application's current snapshot says so.

The UI MUST NOT promise that cancellation will remain available after the next phase transition.

### 11.4 WRITING and later

From WRITING through RECONCILING and POST_VALIDATING:

- show a persistent top-level banner that writing/verification is active;
- state that cooperative cancellation is unavailable;
- disable cancel/replace/navigation actions that would imply the operation stopped;
- keep Help local and safe;
- never kill the worker as a cancellation mechanism;
- never replace the active operation with a new one;
- keep the process open on unresolved adapter fault;
- wait for authoritative application completion when possible.

### 11.5 Completion

Persistent result must summarize:

- success/failure/unresolved;
- whether writing started;
- whether reconciliation completed;
- whether post-validation completed;
- whether final state is authoritative.

The default result SHOULD express those facts in plain language and expose exact booleans/phase/error code in Technical details.

## 12. Persistent-flow text wireframes

### 12.1 Review — Apply configuration

~~~text
Apply configuration — Review

[ ] Nothing has been written yet

Source             my-config.json
Profiles           2, 3
Will change        Managed onboard configuration for the prepared plan
Protected          Profile 1 recovery; sectors 6 and 7

Warnings
  None

[ ] I reviewed this exact prepared operation

> Continue to confirmation
  Technical details

Esc Cancel   ? Help
~~~

Technical details may include target digest, architecture, transport, host guard result, exact preconditions, and operation id subject to privacy rules.

### 12.2 Confirmation

~~~text
Apply configuration — Confirm

[ ] Nothing has been written yet

To continue, type exactly:

APPLY CONFIG

> ______________________________

Confirmation does not bypass safety checks.
The device and prepared plan will be revalidated before writing.

Enter Continue   Esc Cancel   ? Help
~~~

The UI MUST NOT provide a one-click substitute for the phrase.

### 12.3 Active write

~~~text
Apply configuration

[>] Writing changes to onboard memory

Step 4 of 5   Apply
Cancellation is no longer available.

Do not start another hardware action.
The operation will continue to read back and verify the result.

? Help
~~~

“Step 4 of 5” is a named journey step, not a percentage. An implementation may omit the numeric step if it cannot keep the mapping semantically stable.

### 12.4 Reconciliation / verification

~~~text
Apply configuration

[>] Verifying written state

Writing started        Yes
Readback reconciliation In progress
Final validation        Not started

Cancellation is no longer available.

? Help
~~~

### 12.5 Success

~~~text
Apply configuration

[DONE] Changes applied and verified

Writing started         Yes
Readback completed      Yes
Final validation        Passed

The final onboard state is authoritative for this completed operation.

> Return Home
  Technical details

R Refresh   ? Help   Q Exit
~~~

This surface is legal only for authoritative consistent success.

### 12.6 Failure before write

~~~text
Apply configuration

[FAIL] Operation stopped before writing

Writing started         No
Nothing was written.

Reason                   Device or safety conditions changed
Next action               Prepare and review the operation again

> Return to Configuration
  Technical details

R Refresh   ? Help   Q Exit
~~~

“Nothing was written” is allowed only when authoritative state proves writing did not start.

### 12.7 Failure after write

~~~text
Apply configuration

[FAIL] The operation did not complete successfully

Writing started         Yes
Readback/reconciliation <Completed | Not completed>
Final validation        <Completed | Not completed>

The displayed reconciliation and validation values come from the authoritative
terminal result; they are not inferred from FAILED alone.
The final guidance depends on those authoritative facts.
Do not assume the mouse is unchanged without evidence.

> View Technical details
  Return Home

? Help   Q Exit
~~~

The exact available next action depends on the authoritative application result. `writing_started`, `reconciliation_completed`, and `post_validation_completed` MUST each be rendered from authoritative terminal facts. The TUI MUST NOT hardcode “No”/“Not completed” from terminal FAILURE alone and MUST NOT invent a recovery write.

### 12.8 Unresolved adapter fault after write

~~~text
Apply configuration

[!] HARDWARE OUTCOME UNKNOWN

Writing started or may have started.
The UI adapter did not receive authoritative completion.

Keep this process open.
Do not retry the operation while the outcome is unknown.

Cancellation is not available.

? Help
~~~

This is not a terminal SUCCESS or FAILURE surface. It remains an active unresolved state until authoritative completion or another separately governed process-level outcome.

## 13. Apply / Restore task journeys

### 13.1 Apply configuration

Entry point:
- Configuration -> Apply configuration.

Required input:
- config path, classified LOCAL SENSITIVE.

Preparation:
- existing prepare_apply(config_path).

Review primary summary:
- config name;
- enabled profiles;
- user-facing profile names only in LOCAL SENSITIVE review;
- managed change summary;
- warnings;
- protected recovery domain;
- “Nothing has been written yet”.

Technical disclosure:
- target/plan digest;
- compatibility architecture;
- transport;
- eligibility;
- host guard;
- observed preconditions.

Confirmation:
- exact phrase APPLY CONFIG.

Execution:
- existing execute_prepared with existing revalidation and phase authority.

Success:
- “Changes applied and verified” only when consistent terminal success facts are true.

### 13.2 Restore backup

Entry point:
- Backup & Restore -> Restore backup.

Required input:
- backup path, LOCAL SENSITIVE.

Preparation:
- existing prepare_restore_backup(backup_path).

Review primary summary:
- backup name;
- managed sectors summarized for product wording;
- protected sectors;
- explicit statement that Profile 1/recovery protections remain preserved by existing authority;
- “Nothing has been written yet”.

Technical disclosure:
- target digest and exact prepared-operation technical fields.

Confirmation:
- exact phrase RESTORE BACKUP.

Success:
- “Backup restored and verified” only under the same authoritative success requirements.

No “restore everything” wording may imply protected-sector writes.

### 13.3 Restore baseline

Entry point:
- Backup & Restore -> Restore baseline.

Required input:
- none beyond the existing active validated baseline context.

Preparation:
- existing prepare_restore_baseline().

Review primary summary:
- target: active validated baseline;
- managed sectors;
- protected sectors;
- “Nothing has been written yet”.

Confirmation:
- exact phrase RESTORE BASELINE.

Success:
- “Baseline restored and verified” only after authoritative reconciliation and post-validation.

The TUI does not expose or guess factory state.

### 13.4 Switch active profile

Entry point:
- Configuration -> Switch active profile.

This is the currently exposed profile-related operation and remains a volatile mutation, not a PreparedOperation persistent flow.

Required input:
- target profile 1..5.

Primary review/form:
- current profile when known from the last explicit read;
- target profile;
- exact required phrase.

Confirmation:
- Profile 1: BACK TO SAFE
- Profile N, N=2..5: ENTER PROFILE N

Safety wording:
- “This changes the active profile. It does not use the persistent Prepare/Review transaction.”
- Existing identity/validation/host guard/application rules remain authoritative.
- Do not downgrade the exact phrase to yes/no.

Success:
- “Active profile changed to Profile N.”

### 13.5 Read-only refresh/status

Entry point:
- R from Home or any route where no hardware operation/input conflict exists;
- visible Refresh action.

Required input:
- none.

Operation:
- existing explicit read/status path.

Before action:
- “Reading state does not modify the mouse.”

During:
- “Reading current onboard state.”

Success:
- update Home's last-read state and display active profile/current summary from existing typed data.

Failure:
- retain prior snapshot only if it is clearly labeled as older/stale; the latest attempt is READ_FAILED.
- never present the old snapshot as if the failed refresh succeeded.

## 14. Safety-status vocabulary

The canonical semantic markers are ASCII-safe and do not depend on color:

- [OK] Ready / Protected / Completed
- [!] Attention / Read-only / Unresolved
- [X] Blocked
- [>] In progress
- [ ] Not started
- [DONE] Completed and verified
- [FAIL] Failed

Semantic categories:

| Category | Marker | Required text behavior |
| --- | --- | --- |
| Ready | [OK] | State/action is available under the described scope. |
| Protected | [OK] | Explicitly names what remains protected. |
| Attention | [!] | Requires user understanding but is not automatically a failure. |
| Read-only | [!] | Persistent/volatile mutations disabled as applicable. |
| Blocked | [X] | Required action cannot proceed. |
| Running | [>] | Foreground operation is active. |
| Not started | [ ] | Used for read/write/verification facts not yet begun. |
| Completed | [DONE] | Only after required completion authority. |
| Failed | [FAIL] | Only for authoritative terminal failure. |
| Unresolved | [!] | Not terminal success/failure; hardware truth is not authoritative yet. |

Color may reinforce these categories but MUST NOT replace marker + wording.

If an implementation chooses Unicode glyphs on proven terminals, the ASCII marker remains the fallback and test oracle.

## 15. Responsive layout contract

### 15.1 80x24

Use a compact single-column layout.

Required visibility priority:

1. product title/context;
2. device/read truth;
3. active operation and write/cancellation truth;
4. current task title;
5. primary task content;
6. primary action;
7. navigation/help footer.

Technical detail is collapsed/scrollable.

No safety-critical label, confirmation phrase, read-only reason, non-cancellable state, or terminal authority fact may require horizontal scrolling.

Review and confirmation remain fully keyboard reachable.

### 15.2 Above 80x24

A two-column presentation may be used:

- left: primary task/navigation;
- right: latest state, contextual summary, or selected detail.

The second column MUST NOT create a second interaction path with different authority.

### 15.3 Large terminal

Large terminals may keep Technical details visible in a subordinate side panel while preserving the primary task context.

Private detail still requires the same privacy reveal authority.

### 15.4 Below minimum

Below 80x24 the layout becomes safety-constrained.

No active operation:

~~~text
G502 X Onboard

[X] Terminal is smaller than the supported 80x24 minimum.

Enlarge the terminal to use device and configuration actions.
No hardware action is available in this layout.

? Help   Q Exit
~~~

Active pre-write operation:
- show operation title;
- show “Nothing has been written yet” only when authoritative;
- show whether cooperative cancellation remains available;
- allow only the already-authorized safe cancellation/back behavior plus Help;
- do not expose a confirmation submit control while constrained.

Active WRITING+ operation:
- show “Writing/verification is still in progress”;
- show “Cancellation is unavailable”;
- do not allow new hardware actions;
- do not allow normal Q/Esc behavior to make the operation appear stopped;
- instruct user to enlarge the terminal for full detail;
- retain active lifecycle state until authoritative completion.

The existing rule that hardware-action shortcuts are disabled below minimum MUST NOT be weakened.

## 16. Error UX

Default error presentation is privacy-safe. Classified local/private detail appears only on an allowed surface.

| Condition | Primary copy | Allowed next action | Forbidden implication |
| --- | --- | --- | --- |
| Undifferentiated failed read | “Device state could not be read.” | Check connection; R retry; Help; permitted Diagnostics | Do not infer no-device, unsupported-device, host-guard, transport, firmware, or another cause from PRIVATE/raw strings. |
| No supported device — only when an existing typed privacy-safe result independently identifies this condition | “No supported device state was found.” | Check connection; R retry; Diagnostics/Help | Do not imply this cause from generic BACKEND_FAILURE or private detail. |
| Device unavailable — only when an existing typed privacy-safe result independently identifies this condition | “The device is unavailable.” | R retry after fixing connection | Do not infer availability cause from generic/private text. |
| Competing Logitech writer — only when an existing typed privacy-safe result independently identifies this condition | “Close G HUB and Logitech Onboard Memory Manager before continuing.” | Close competing writer, prepare again | No “continue anyway”; do not infer the writer from PRIVATE/raw text. |
| Read-only compatibility | “This device is read-only.” | Inspect/diagnose/refresh | No persistent or profile mutation enabled against policy. |
| Invalid configuration | “Configuration could not be prepared.” | Fix config, plan/prepare again | Do not expose LOCAL SENSITIVE path/detail on SHAREABLE surface. |
| Preparation rejected | “This operation cannot be prepared safely.” | Read typed safe reason; refresh/fix; prepare again | No executable prepared capability. |
| Stale preparation | “State changed since review. Prepare and review again.” | Return to fresh prepare flow | No reuse of confirmation/preparation. |
| Pre-write failure | “Operation stopped before writing.” | Fix condition; prepare again | “Nothing was written” only if authoritative. |
| Authoritative failure after write | “The operation did not complete successfully.” | Inspect authoritative facts/help | Do not claim unchanged state. |
| Adapter fault during WRITING+ | “HARDWARE OUTCOME UNKNOWN.” | Keep process open; await authoritative completion | Do not label terminal FAILURE/SUCCESS; do not retry. |
| Busy | “Another hardware operation is already active.” | Wait for active operation | Do not queue a second write. |

Technical details may show ErrorCode and exact technical state if allowed by privacy classification.

Raw exception text MUST NOT be promoted into default shareable copy.

### 16.1 Privacy-safe cause authority

Condition-specific product copy may be used only when an existing typed result available to the current privacy surface independently identifies that condition.

If a read or preparation fails without an existing privacy-safe typed distinction, use cause-neutral product copy such as:

~~~text
[X] Device state could not be read

The application could not obtain a usable device state from this read.

Check the connection and try again.
Open Help or Diagnostics for any additional information that is available
to the current privacy surface.

R Try again   ? Help   Q Exit
~~~

P7.2 MUST NOT recover a public cause by parsing raw exception text, PRIVATE diagnostic detail, humanized runner strings, or any other unclassified text channel.

If P7.2 requires a finer product distinction such as no-device, device-unavailable, G HUB active, or Onboard Memory Manager active and that distinction is not already available as privacy-safe typed application data:

    STOP AND RETURN TO ORCHESTRATION

P7.2 is not authorized to solve that gap by duplicating backend detection, calling backend/device code directly, or silently changing ApplicationFacade. A future application-error-contract change requires a separately authorized orchestration decision.

## 17. First-run and empty-state contract

Phase 7 does not add a setup wizard.

“First run” means first presentation in the TUI session.

The initial screen:

- performs zero hardware calls automatically;
- makes “Read current onboard state” the obvious safe action;
- explains that the read does not modify the mouse;
- does not claim recognition/readiness before the read;
- does not imply cached/local state is fresh physical truth.

If an existing prerequisite such as a valid local setup baseline is missing and the existing application read returns a refusal/error:

- READ_FAILED remains cause-neutral unless existing privacy-safe typed authority independently identifies a more specific cause;
- show only product copy supported by the typed result available to the current privacy surface;
- otherwise use the generic privacy-safe failed-read fallback from §16.1;
- never infer no-device, device-unavailable, G HUB/Onboard Memory Manager activity, transport failure, firmware failure, or another public cause from PRIVATE/raw text;
- explain the next existing workflow in Contextual Help only when the current typed/privacy authority supports that guidance;
- do not silently create/setup a baseline;
- do not add a hidden setup write/read sequence;
- do not broaden P7.1 into an onboarding backend redesign.

## 18. Technical disclosure contract

Technical details are contextual, not a separate dumping ground.

The disclosure should be attached to the current context:

Home/status:
- exact last operation action/id;
- raw technical state available from the permitted typed payload;
- current privacy class.

Review:
- target digest;
- architecture;
- transport;
- write eligibility;
- host guard state;
- observed preconditions;
- exact persistent enum phase if useful.

Active operation:
- exact phase;
- cancellation_allowed;
- operation id;
- error code when present.

Result:
- terminal phase;
- writing_started;
- reconciliation_completed;
- post_validation_completed;
- error code.

Technical disclosure MUST NOT make private diagnostic information shareable.

## 19. Formal acceptance matrix

| Scenario | User goal | Visible primary state | Permitted actions | Forbidden actions | Expected safety message | Existing invariant preserved |
| --- | --- | --- | --- | --- | --- | --- |
| First launch | Understand where to start | Device state not read yet | Read state, navigate local areas, Help, Exit | Automatic hardware read | “Reading state does not modify the mouse.” | No background polling |
| No refresh yet | Know whether screen is physically current | NEVER_READ | R, Help, local navigation | “Ready”/fresh-device claim | “Device state not read yet.” | Cached presentation != physical truth |
| Refresh success | Read current onboard state | READ_OK + active profile/state | Navigate tasks, R again, Help | Hidden extra read | “State read successfully.” | Explicit foreground read only |
| Refresh failure | Understand failed read | READ_FAILED + cause-neutral failure state unless existing privacy-safe typed authority identifies more | Retry, Help, permitted Diagnostics | Infer cause from PRIVATE/raw strings; treat old data as fresh; claim no-device/host-guard/etc. without typed authority | “Device state could not be read.” | Typed application error + privacy authority |
| Device read-only | Inspect without writing | Read-only status/reason | Read-only tasks, Help, Refresh | Persistent prepare/profile mutation if policy disallows | “This device is read-only.” | Unknown/unvalidated targets remain read-only |
| No device — only when identified by existing privacy-safe typed authority | Recover from typed absence | No supported device state | Retry, Help | Infer no-device from generic/private failure text; claim supported mouse available | “No supported device state was found.” | No fabricated compatibility |
| Specific read/host-guard condition — only when identified by existing privacy-safe typed authority | Act on a known typed cause | Condition-specific product state | Only actions allowed by existing authority | Infer cause from PRIVATE/raw strings; make the condition mandatory when no typed distinction exists | Condition-specific copy supported by the typed result | Privacy-safe typed authority only |
| Help | Understand current screen | Context-specific help | Navigate help, close help | Backend/facade call from help | Explicit “nothing written / cancellation” answer | Help is presentation-only |
| Technical disclosure | Inspect exact engineering state | Secondary details | Open/close allowed detail | Promote private detail to shareable | Privacy label when required | Privacy classes |
| Apply review | Understand intended write | What changes + what remains protected | Acknowledge, details, cancel | Execute without review/confirm | “Nothing has been written yet.” | Prepare is read-only; protected recovery |
| Typed confirmation | Authorize reviewed intent | Exact phrase + no-write truth | Type phrase, submit exact match, cancel | Yes/no substitute; remembered confirmation | “Type exactly: APPLY CONFIG.” | Exact confirmation contract |
| Pre-write cancellation | Stop before persistence | Cancellable pre-write stage | Esc/cancel when application allows | Claim cancellation after boundary | “Nothing has been written yet.” | Cooperative cancellation only before WRITING |
| WRITING+ non-cancellable | Understand irreversible safety stage | Writing/verification active | Help, details, await result | Cancel worker, replace op, refresh, exit-as-cancel | “Cancellation is no longer available.” | WRITING+ truth and transaction continuation |
| Successful persistent completion | Know result is authoritative | DONE + verified facts | Home, details, refresh | Success without post-validation | “Changes applied and verified.” | Reconciliation + post-validation success gate |
| Failed persistent completion | Know what is authoritative | FAIL + writing/reconciliation/validation facts | Help/details/safe next flow | Assume unchanged after write | Copy depends on writing_started/result | Authoritative terminal result |
| Privacy downgrade | Return to shareable surface | SHAREABLE | Shareable tasks | Retain/render disallowed payload | Sensitive fields disappear | Clear, not merely hide, sensitive state |
| Focused input | Type paths/confirmation reliably | Input owns printable keys | Type, Tab, Shift+Tab, Enter per input | Letter shortcuts firing hardware actions | Context hint remains visible | No shortcut collision |
| 80x24 | Complete essential task | Single-column full safety truth | All supported keyboard flows | Horizontal safety-content dependency | Full labels/confirmation reachable | Minimum supported layout |
| Below minimum | Avoid unsafe cramped interaction | Constrained safety screen | Help/Exit when idle; safe existing cancellation where allowed | New hardware action; new confirm submit | “Enlarge terminal to use device actions.” | Existing constrained shortcut restrictions |
| Large terminal | See richer context | Primary + secondary panel | Same actions as baseline layout | Extra hardware authority from layout | Same semantic labels | Layout does not change behavior |
| NO_COLOR | Understand all states without color | ASCII marker + text | All normal actions | Color-only meaning | Ready/read-only/blocked/running/success/failure text | Semantic parity |
| Clean exit idle | Leave application | Idle | Q/Exit | Hidden hardware operation | No special warning required | No hardware action created |
| Clean exit pre-write execution | Stop safely if currently cancellable | Active pre-write + cancel truth | Q/Esc routes to cooperative cancellation where application allows | Process-kill masquerading as cancel | “Cancelling before write…” only after request | CancellationToken/application authority |
| Exit during WRITING+ | Avoid false cancellation | Active non-cancellable | Help, await result | Normal Q/Esc termination-as-cancel | “Operation is still running; cancellation unavailable.” | WRITING+ non-cancellable truth |
| Stale operation | Re-establish reviewed intent | Stale/refused | Prepare again | Reuse consumed/stale preparation | “State changed since review; prepare again.” | Revalidation + single-use preparation |
| Profile switch | Change active profile | Current/target profile + phrase | Exact confirmation, cancel/back | Generic yes/no | ENTER PROFILE N or BACK TO SAFE | Existing volatile confirmation contract |
| Restore backup | Restore managed domain | Review backup/managed/protected | Review, exact confirm, execute | Protected-sector implication | “Profile 1 recovery and sectors 6/7 remain protected.” | Recovery boundary |
| Restore baseline | Return managed domain to active baseline | Review target/protection | Review, exact confirm, execute | Guess factory state | “Target: active validated baseline.” | Existing baseline authority |
| Unresolved adapter fault | Avoid unsafe retry | HARDWARE OUTCOME UNKNOWN | Help, keep process open, await authority | Terminal success/failure claim; retry | “Do not retry while outcome is unknown.” | No fabricated terminal truth |

## 20. Product acceptance criteria

P7.2/P7.3 implementation is acceptable only if tests can objectively prove all of the following.

### 20.1 New-user comprehension

Without seeing internal phase names, the default UI allows a new user to determine:

1. whether physical state has been read in this session;
2. whether the latest read succeeded;
3. whether the target is ready for supported tasks, read-only, or blocked/refused;
4. the current active profile when known;
5. the next safe action;
6. whether any operation is pending;
7. whether persistent writing has started;
8. how to go back, refresh, get Help, and exit.

### 20.2 Before persistent execution

The user can determine:

- operation type;
- source/target;
- what changes;
- what remains protected;
- warnings;
- nothing has been written yet;
- exact required confirmation;
- technical detail is available but secondary.

### 20.3 During persistent execution

The user can determine:

- which human journey step is active;
- whether writing has started;
- whether cooperative cancellation remains available;
- whether the UI is waiting on authoritative reconciliation/validation.

### 20.4 After persistent execution

The user can determine:

- success vs authoritative failure vs unresolved outcome;
- writing_started;
- reconciliation_completed;
- post_validation_completed;
- whether the final state is authoritative.

## 21. Non-goals

P7.1 and this Phase-7 UX contract do not authorize:

- GUI;
- web frontend;
- mouse-first interface;
- hardware-support expansion;
- backend rewrite;
- ApplicationFacade redesign;
- new persistent action;
- new background polling;
- relaxed confirmation;
- generic yes/no replacement for exact phrases;
- force/unsafe/bypass path;
- Profile 1 or sectors 6/7 write expansion;
- firmware/receiver/DFU work;
- release-verifier redesign;
- automatic recovery writes;
- setup wizard/backend onboarding expansion;
- decorative dashboard complexity for its own sake;
- parsing human-formatted runner strings to recreate typed safety truth.

## 22. Later implementation boundary — P7.2 / P7.3

P7.1 itself changes no implementation.

Likely primary implementation surfaces:

- g502x_onboard/tui/app.py
- g502x_onboard/tui/view.py

Potential small typed presentation changes, only when justified by the implementation work item:

- g502x_onboard/tui/model.py
- g502x_onboard/tui/events.py
- g502x_onboard/tui/update.py

The current runner converts typed ProbeSnapshot/StatusSnapshot results into human-formatted strings before ApplicationCompleted reaches the model. The product Home contract should preserve typed truth rather than parse those strings.

Therefore:

- P7.2 should first determine whether existing typed events/model paths can carry the required Home fields without changing runner.py.
- If a minimal TUI-adapter-only runner.py change is required solely to forward already-existing typed application result data, that requirement must be explicitly authorized by the P7.2 orchestration/write set.
- Such a change MUST NOT alter facade calls, hardware operation count, operation serialization, or persistent semantics.
- If P7.2 is not authorized to touch runner.py, it MUST NOT work around the boundary by parsing completion strings.

Any change to application/, backend/device, persistent implementation, or release tooling requires a fresh orchestration decision and is not authorized by this specification.

The same STOP boundary applies to error classification. If P7.2 needs a finer public distinction (for example no-device, device-unavailable, G HUB active, or Onboard Memory Manager active) that is not already present in privacy-safe typed application data, it MUST STOP AND RETURN TO ORCHESTRATION rather than parse PRIVATE/raw text or silently change ApplicationFacade.

## 23. Validation strategy for P7.2 / P7.3

UX implementation MUST preserve semantic safety tests. Screenshots/snapshots may supplement but never replace semantic assertions.

### 23.1 Semantic state tests

Add/update deterministic state tests for:

- NEVER_READ;
- READING;
- READ_OK;
- READ_FAILED;
- ready/read-only/blocked human status;
- prepared/review/confirmation human journey mapping;
- pre-write writing_started=false copy;
- WRITING+ non-cancellable copy;
- consistent success gating;
- failure-after-write facts;
- unresolved adapter fault;
- privacy downgrade clearing.

### 23.2 Navigation tests

Exercise with keyboard only:

- Home selection;
- Configuration;
- Backup & Restore;
- Diagnostics;
- Help from every required context;
- Esc behavior;
- R behavior;
- Q safe-exit behavior;
- route accelerators if retained.

### 23.3 Focus/shortcut tests

Prove:

- printable shortcut letters are consumed by focused Input;
- ? does not steal a printable input character;
- Tab/Shift+Tab reach every required visible control;
- hidden/disabled controls cannot receive Enter;
- Esc priority preserves safety semantics;
- exact confirmation Enter cannot double-submit execution.

### 23.4 Layout tests

Test at:

- 80x24;
- >80x24;
- a large terminal;
- below minimum;
- resize from supported size to below minimum during pre-write;
- resize from supported size to below minimum during WRITING+.

Safety labels and exact confirmation MUST remain semantically testable independent of pixels.

### 23.5 NO_COLOR tests

With NO_COLOR set, assert that text/markers distinguish:

- ready;
- read-only;
- blocked;
- in progress;
- non-cancellable;
- success;
- failure;
- unresolved;
- PRIVATE.

### 23.6 Privacy tests

Preserve and extend current tests so:

- shareable view models contain no disallowed local/private data;
- local-sensitive review remains local-sensitive;
- PRIVATE is labeled before reveal;
- downgrade clears sensitive state even when widgets are hidden by responsive layout;
- technical disclosure never promotes raw private detail.

### 23.7 WRITING+ tests

Prove:

- no cancel effect is emitted from WRITING/RECONCILING/POST_VALIDATING;
- Help does not mutate lifecycle;
- refresh/new hardware actions are unavailable;
- Q/Esc do not fabricate cancellation or terminal state;
- unresolved adapter fault remains active/non-terminal;
- authoritative later completion still wins.

### 23.8 No-background-polling tests

Mount, navigate, focus, Help, Technical details, and resize without pressing Refresh or starting a foreground operation.

Backend/facade hardware call counters must remain unchanged.

Explicit Refresh produces only the intended foreground read path. If P7.2 changes the read composition, its exact call count must be deliberately specified and tested; no implicit reads are allowed.

### 23.9 Extracted release and full CI

Later implementation validation includes:

- core no-Textual path;
- optional locked Textual path;
- TUI test harness;
- extracted release smoke;
- Linux x64;
- Windows x64;
- Windows x86;
- deterministic release digest equality;
- full existing unit suite;
- selftest/help/capabilities commands already required by repository authority.

The semantic safety suite MUST remain primary. Snapshot/layout tests are supplemental.

## 24. Hardware strategy

P7.1 hardware access:

    NONE

No physical smoke is required for this documentation-only work item.

A final Phase-7 UX integration may require one read-only physical smoke because navigation/presentation will materially change.

No new persistent physical smoke is required merely for UX presentation changes unless a separately approved implementation changes write ordering, transport behavior, recovery behavior, supported hardware, or persistent semantics.

## 25. Review gate

Before P7.1 merge, an independent reviewer should verify:

- the IA is task-oriented rather than enum-oriented;
- Home clearly distinguishes “not read” from physical truth;
- “Ready” does not overclaim write authority;
- persistent safety meaning is preserved;
- exact confirmations remain unchanged;
- Profile 1 and sectors 6/7 protections remain visible and intact;
- technical detail remains available through progressive disclosure;
- privacy classifications remain enforceable;
- contextual Help is state-aware and hardware-free;
- focus/shortcut rules do not collide with text input;
- WRITING+ cannot be presented as cancellable;
- unresolved adapter faults cannot become terminal failures/successes in presentation;
- 80x24 remains fully usable;
- below-minimum mode remains safety-limited;
- acceptance criteria are concrete enough to drive P7.2/P7.3 tests;
- the specification does not silently expand ApplicationFacade/application/backend authority.

P7.1 MUST NOT be merged before that independent review.

## 26. Traceability to current safety tests

The design intentionally preserves the current tested semantics:

- TUI hardware access remains confined to the facade runner boundary.
- Pure model/update/view state remains independently testable.
- Explicit refresh remains the only idle refresh intent.
- A second hardware operation remains rejected while one is active.
- Prepared review and confirmation remain tied to the current operation issuance.
- Duplicate confirmation cannot issue a second execution.
- Privacy downgrade clears disallowed prepared/path/detail state.
- Read-only presentation remains explicit.
- NO_COLOR semantics remain text-visible.
- Focused text inputs consume printable shortcut letters.
- 80x24 remains supported.
- Below-minimum layout blocks hardware actions.
- Pre-write cancellation uses cooperative application authority.
- WRITING+ cancel requests emit no cancellation effect.
- Persistent success still requires writing + reconciliation + post-validation evidence.
- Worker transport faults after WRITING remain unresolved rather than fabricated terminal truth.
- No progress percentage is invented.

P7.2/P7.3 may change visual composition and copy tests, but MUST preserve these semantic assertions or replace them with stronger tests proving the same invariant.

## 27. Design decision summary

The Phase-7 product model is:

- Home answers “what is true now, based on the last explicit read?”.
- Configuration answers “what do I want to configure?”.
- Backup & Restore answers “which existing recovery/restore task do I want?”.
- Diagnostics answers “what technical/read-only evidence do I need?”.
- Contextual Help answers “what is this state, what is safe, what can I press, has anything been written, can I cancel?”.

The interface foreground is human task/safety language.

Technical engineering state remains exact, available, and classified behind progressive disclosure.

No part of this design changes hardware authority.

v0.2.0 TAG/RELEASE: NOT AUTHORIZED
