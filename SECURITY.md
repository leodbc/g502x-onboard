# Security and safety reporting

G502 X Onboard modifies persistent onboard **configuration**. It does not flash
mouse firmware, receiver firmware, enter DFU, or intentionally write outside the
managed onboard-profile domain.

Normal writes are restricted to Sector 0, Profiles 2-5 and macro sectors 8-15.
Profile 1 and sectors 6/7 are protected recovery state. Every sector write is
re-read and reconciled before the transaction proceeds.

See [docs/SAFETY.md](docs/SAFETY.md) for the full model.

## Reporting a safety or security issue

Use GitHub private vulnerability reporting when it is enabled for the
repository. If private reporting is unavailable, open a minimal public issue
without sensitive details and ask the maintainer for a private channel.

For a private report, include the exact tool version/commit, device connection
mode, command and sanitized terminal output. Do not include credentials, unit
IDs, serial numbers, private fingerprints, local baselines, raw sectors or
personal macro contents.

For public hardware context, prefer the shareable report commands:

```bash
python g502x.py report probe probe-report.json
python g502x.py report device device-report.json --state
python g502x.py report check <report.json>
```

Raw `probe --json` and `debug export` are private diagnostic surfaces.

The software is provided without warranty under GPL-3.0-only.
