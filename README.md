# G502 X Onboard

Configure the **Logitech G502 X LIGHTSPEED** from JSON and store the result
directly in onboard memory.

G502 X Onboard compiles configuration into Profile Format 3 / Macro Format 1
data, validates the result, and writes it with readback and recovery safeguards. After programming, the configured profiles,
DPI, G-Shift bindings and supported macros run without G HUB or a resident
companion process.

**Current source version:** `0.1.0`

Write support is intentionally narrow: it is hardware-validated on one G502 X
LIGHTSPEED using the original LIGHTSPEED receiver, PID `C547`, subdevice `1`,
and the firmware documented in [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md).
Unknown devices, transports and firmware remain read-only.

## Why this project exists

Existing open-source Logitech projects already cover parts of this space. This
project focuses specifically on reproducible **configuration-as-code**:

- deterministic config -> byte plan compilation;
- one global onboard macro store with deduplication;
- automatic cross-page Macro Format 1 JUMP linking;
- structural validation before and after writes;
- per-device local safety baselines;
- transactional backups and byte-for-byte write reconciliation;
- explicit recovery isolation for Profile 1 and sectors 6/7;
- privacy-minimized reports for sharing hardware evidence.

It is not intended to replace mature device managers such as Solaar or
libratbag, or GUI-focused tools.

## Quick start

Python 3.12 is the validated development/runtime version.

The physical write-validation work for this release was performed from Windows.
The source/release carries validated x64 and x86 hidapi 0.15.0 DLLs, and the
loader verifies the selected DLL path, version and SHA-256 before HID
enumeration.

Linux participates in software/release CI but has not been physically validated
for persistent writes by this project. For read-only experimentation on Linux,
install your distribution's hidapi/hidraw runtime first (for example
`libhidapi-hidraw0` on Debian/Ubuntu). macOS has not been physically validated.

```bash
python -m pip install -r requirements.txt
python g502x.py --version
python g502x.py selftest
```

Before direct hardware access, fully close **Logitech G HUB** and
**Logitech Onboard Memory Manager** if either is running.

Start read-only:

```bash
python g502x.py probe
```

Check the reported active profile. Initial setup requires **Profile 1 SAFE** to
already be active. If it is not, switch the mouse to its existing onboard
Profile 1 using its normal profile-selection method, close any Logitech
configuration software again, and rerun `probe`.

Then capture the local safety baseline:

```bash
python g502x.py setup
```

After setup, the tool can return to the recovery profile explicitly:

```bash
python g502x.py profile safe
```

Copy [examples/basic.json](examples/basic.json), edit it for your own profiles,
then inspect the exact plan before writing:

```bash
python g502x.py plan my-config.json
python g502x.py apply my-config.json
python g502x.py validate
python g502x.py status
```

`apply` requires Profile 1 SAFE, explicit confirmation, and a matching local
baseline. It creates a private pre-write backup, writes only the managed
programmable domain, and reconciles each sector with a fresh byte-for-byte
readback.

## Configuration example

```json
{
  "format": 1,
  "profiles": {
    "2": {
      "settings": {
        "name": "WORK",
        "polling_rate_hz": 1000,
        "dpi": [800, 1600, 3200],
        "default_dpi": 1600,
        "shift_dpi": 800
      },
      "buttons": {
        "G4": "copy",
        "G5": "paste"
      },
      "g_shift_button": "G6",
      "g_shift": {
        "G4": "volume_up"
      }
    }
  }
}
```

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for the complete format and
supported actions.

## Shareable hardware reports

Before setup:

```bash
python g502x.py report probe probe-report.json
python g502x.py report check probe-report.json
```

After setup:

```bash
python g502x.py report device device-report.json --state
python g502x.py report check device-report.json
```

These formats intentionally omit unit IDs, serial numbers, private fingerprints,
raw sectors and user profile names. Raw `probe --json` and `debug export`
outputs are private diagnostics.

## Safety boundary

Normal writes are limited to:

- Sector 0 directory state;
- Profiles 2-5;
- macro sectors 8-15.

Profile 1 and sectors 6/7 are protected recovery state. This project does not
write firmware, receiver firmware or DFU state. See
[docs/SAFETY.md](docs/SAFETY.md).

## Documentation

- [CONFIGURATION.md](docs/CONFIGURATION.md) — config format and command workflow
- [PROTOCOL.md](docs/PROTOCOL.md) — byte-level protocol findings
- [HARDWARE_VALIDATION.md](docs/HARDWARE_VALIDATION.md) — what was physically tested
- [SAFETY.md](docs/SAFETY.md) — write authority, recovery and privacy model
- [PROVENANCE.md](docs/PROVENANCE.md) — inherited code and dependency provenance
- [RELEASE_VERIFICATION.md](docs/RELEASE_VERIFICATION.md) — verify release ZIPs and SBOM metadata

## Lineage and related work

The low-level HID++ transport under `libs/` is derived from
[lexr1/omm.py](https://github.com/lexr1/omm.py) under GPL-3.0. Other projects,
including libratbag, Solaar and independent G502 X tooling, provided useful
protocol comparison and corroboration. See [NOTICE.md](NOTICE.md),
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and
[docs/PROVENANCE.md](docs/PROVENANCE.md).

No claim of being the first G502 X configuration tool is made.

## Repository history

Hardware research and early development used a separate working repository
because raw captures, per-unit identifiers and recovery state were involved.
This repository intentionally begins from a reviewed clean source snapshot
rather than importing device-specific development history.

## License

GPL-3.0-only. See [LICENSE](LICENSE).

This is an unofficial community project and is not affiliated with, endorsed by,
or supported by Logitech.
