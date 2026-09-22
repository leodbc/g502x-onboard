# Supported devices

Status: 2026-09-22.

| Device / transport | Read-only | Persistent writes | Evidence |
| --- | --- | --- | --- |
| G502 X LIGHTSPEED via original LIGHTSPEED receiver, PID `C547`, subdevice `1` | Yes | Yes, only when exact device/firmware identity matches the local baseline | **Hardware validated (n=1)** |
| Same memory geometry on a different firmware revision | Yes | No by default | Requires new hardware evidence |
| Wired G502 X | Read-only discovery only | No | Not locally validated |
| G502 X PLUS / other Logitech mice | Read-only discovery only | No | Separate target; no write claim |

## Locally validated target

The write-enabled evidence is limited to the physical unit used during
development:

- Logitech G502 X LIGHTSPEED
- HID++ 2.0
- receiver VID `046D`
- receiver PID `C547`
- subdevice/index `01`
- device model ID `409F`
- active firmware `MPM 30.00.B0014`
- Memory Model 1
- Profile Format 3
- Macro Format 1
- 5 profiles
- 16 sectors
- 255 bytes per sector

A matching PID or descriptor alone does **not** authorize writes. The tool also
requires a stable per-unit identity, the validated device/firmware class and a
matching local setup baseline.

Please contribute new hardware evidence read-only first. See
[CONTRIBUTING.md](CONTRIBUTING.md).


## Host validation scope

The physical persistent-write experiments and final read-only release smoke for
the supported target were performed from Windows. The public CI matrix also
tests Linux and both 64-bit and 32-bit Windows Python environments, but CI
without attached hardware is software/release validation rather than evidence
of physical write support on another host OS.
