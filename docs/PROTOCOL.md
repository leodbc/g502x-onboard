# Protocol notes

This document separates **local hardware evidence** from external corroboration and unresolved areas.

## Scope

Local hardware target:

- Logitech G502 X LIGHTSPEED
- receiver PID `0xC547`
- receiver subdevice `0x01`
- memory model 1
- Profile Format 3
- Macro Format 1
- 16 live sectors
- 255 bytes per sector
- 2 out-of-box (ROM) profiles reported by descriptor

The compiler intentionally uses sectors 8–15 for managed macro payload. Sectors 6/7 are reserved as recovery even though external implementations treat the wider post-profile region as macro-capable.

## Hardware-validated findings

### Transport / memory

- HID++ feature `0x8100` ONBOARD_PROFILES is readable/writable through the C547 receiver/subdevice 1.
- The final 16-byte read window starts at offset 239 for a 255-byte page.
- Profile/directory CRC is CRC-16/CCITT-FALSE, seed `0xFFFF`, over bytes 0..252 with the stored CRC at bytes 253..254.
- Our managed macro allocator also writes that CRC on sectors 8..15, but Macro Format 1 execution does not universally require a materialized CRC on this device.
- `WRITE_END` acknowledgement alone is not authoritative; byte-for-byte readback is.
- A post-`WRITE_END` transport/readback failure can occur after the write actually committed. Reconnect and reconcile before retrying.

### Profile Format 3

Locally mapped:

- byte 0: report-rate code (`01/02/04/08` => 1000/500/250/125 Hz)
- byte 1: default DPI index
- byte 2: DPI-shift index
- bytes 3..12: five uint16 little-endian DPI stages
- NORMAL button table starting at 0x20
- G-Shift table starting at 0x60
- profile name at 0xA0..0xCF UTF-16LE
- Profile 1–5 live sectors at 1–5

### Direct bindings

Validated:

- Direct mouse: `80 01 mask_hi mask_lo`
- Direct keyboard: `80 02 modifiers usage`
- Direct Consumer: `80 03 usage_hi usage_lo`
- G-Shift activator: `90 0B 00 00`

### Macro Format 1

Validated locally:

| Opcode | Meaning | Encoding |
| --- | --- | --- |
| `01` | wait for release | 1 byte |
| `02` | repeat while pressed | 1 byte |
| `20` | vertical wheel | opcode + signed amount + 00 |
| `21` | horizontal wheel | opcode + signed amount + 00 |
| `40` | delay | opcode + uint16 BE milliseconds |
| `43` | key down | opcode + modifier + HID usage |
| `44` | key up | opcode + modifier + HID usage |
| `45` | Consumer down | opcode + uint16 BE usage |
| `46` | Consumer up | opcode + uint16 BE usage |
| `60` | JUMP | opcode + uint16 BE page + uint16 BE offset |
| `61` | XY | observed/validated experimentally; semantics not productized |
| `FF` | END | 1 byte |

The five-byte JUMP was observed in G HUB output and then emitted manually by this project:

    sector 8: type A + 60 00 09 00 00
    sector 9: type B + END
    observed runtime: AB

The compiler allocator later generated its own cross-page JUMP inside a long text macro; that complete chain also passed hardware validation.

### Macro-page storage variants

Both forms below were physically validated on the same G502 X:

- raw/no-CRC page: the manual cross-page JUMP experiment filled sectors
  8/9 with `FF`, inserted bytecode/JUMP/END, did not materialize a trailing
  CRC, and physically produced `AB`;
- CRC-materialized page: the compiler allocator writes CRC into bytes 253..254 and
  allocator-generated cross-page macros physically executed.

Reader/validator consequence:
- sectors 6..15 are Macro Format 1-addressable for external-state validation;
- `raw_no_crc` is a storage state, not automatic corruption;
- a referenced raw page is accepted only when the full reachable VM chain
  decodes structurally and reaches a valid terminator/path;
- erased pages remain valid storage only while not referenced as an active macro.

Writer consequence:
- this project continues to allocate/write only sectors 8..15;
- compiler pages remain 253-byte payload + 2-byte CRC until a separate change
  is hardware validated;
- sectors 6/7 stay protected from normal writes.

### Global macro addressing

Validated:

- profiles can point to macro sectors outside a per-profile pair,
- two profiles can point to the exact same macro address,
- the same physical macro can execute from P2 and P3,
- automatic physical-page fallthrough does not happen,
- explicit JUMP is required.

## Observed negative in the tested G HUB workflow

These statements are intentionally narrow.

### G HUB Start Application

In a controlled G HUB comparison, the native Start Application assignment did **not** become a self-contained autonomous onboard action. No executable/path payload was discovered in the active onboard representation and it did not execute with G HUB closed.

This does not mean keyboard HID cannot cause an OS to launch something. It means the tested G HUB feature itself was not an onboard process-execution primitive.

### G HUB Text and Emojis

In the same controlled comparison, G HUB Text and Emojis became NOOP in onboard mode for short ASCII, long ASCII, accented text and emoji.

Keyboard-representable text still works because this compiler lowers it into keyboard HID Macro VM events.

## External evidence / local validation pending

### Macro mouse press/release `0x41/0x42`

This project originally left these opcodes unresolved rather than guess their operands.

Current external implementations provide concrete evidence that they are mouse press/release events carrying a mouse-button bitmask. In particular, libratbag PR #1895 and Deadband's Logitech macro implementation expose this family.

The core compiler still leaves them disabled until we run one local reversible hardware test.

### ROM / out-of-box pages

Deadband documents the HID++ onboard-memory ROM directory at `0x0100` and factory profile pages referenced from it. Its G502 X reports two OOB profiles.

The current read-only path captures that ROM/OOB evidence during `probe`/`setup`, but does not blindly write ROM bytes back. ROM copies may not carry a CRC that validates using the live-sector formula.

## Host-side semantics / exact app transport not proven locally

- OBS integration
- Discord integration
- Overwolf integration
- similar G HUB semantic app actions

The controlled G HUB comparison did not demonstrate an autonomous onboard encoding for those actions. Their exact G HUB IPC/plugin transport was not reverse-engineered because it does not expand the autonomous machine inside the mouse.

## No onboard primitive demonstrated

General Unicode/emoji injection has no locally demonstrated autonomous primitive. The compiler reports it as `HOST_REQUIRED`.

## External cross-checks

Relevant current upstream work:

- libratbag: https://github.com/libratbag/libratbag
- Deadband: https://github.com/broroeror/gamesir-linux-tools
- hidpp: https://github.com/cvuchener/hidpp
- Solaar: https://github.com/pwr-Solaar/Solaar
- omm.py: https://github.com/lexr1/omm.py

Where external projects and local evidence disagree, this project documents the scope instead of declaring either side universally wrong.
