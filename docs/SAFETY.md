# Safety model

G502 X Onboard treats persistent onboard configuration as a hardware-state
transaction.

## Managed write domain

Normal compiler writes are restricted to:

- Sector 0 directory state;
- Profiles 2-5;
- macro sectors 8-15.

Profile 1 and sectors 6/7 are protected recovery state.

The project does not write mouse firmware, receiver firmware, DFU state, or
arbitrary HID++ features outside the onboard-profile workflow.

## Write authority

Persistent writes require all of the following:

- a compatible Profile Format / Macro Format / memory geometry;
- a tested transport;
- the locally validated device/firmware class;
- a stable HID++ per-unit identity;
- a local setup baseline bound to that physical unit;
- the expected recovery state;
- G HUB and Logitech Onboard Memory Manager closed;
- Profile 1 SAFE for transactional apply/restore flows.

A matching USB PID or descriptor by itself is insufficient.

Unknown units, firmware and transports remain read-only.

## Readback is authoritative

Each sector write is freshly read back and compared byte-for-byte.

If the transport reports an ambiguous error after WRITE_END, the tool reconnects
to the exact unit and classifies the sector from observed bytes rather than
blindly retrying a write that may already have committed.

## Baselines and backups

`setup` captures a per-device local baseline without writing the mouse. Local
baselines and backups contain private onboard state and live under
`G502X_HOME` (default `~/.g502x`).

Apply and restore operations create safety backups and bind them to the device
baseline fingerprint. Manifest hashes cover all 16 sectors before a backup is
accepted for restore.

Private-state files use atomic replacement; symlink-backed state is rejected;
hardware transactions are serialized with an OS-backed operation lock.

## Privacy

Do not publish:

- raw unit IDs or serial numbers;
- private fingerprints;
- local baselines/backups;
- raw sector dumps;
- private `probe --json`;
- `debug export` output;
- personal macro/profile contents.

Use `report probe` or `report device`, then `report check`, for shareable
hardware evidence.

## Recovery

The tool never silently rewrites Profile 1 or sectors 6/7 during ordinary
configuration. Returning to the active local baseline uses:

```bash
python g502x.py baseline restore
```

and requires explicit confirmation.
