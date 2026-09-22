# Hardware validation

This document records **local hardware evidence**, not a universal compatibility
claim.

## Test target

Validation was performed on one Logitech G502 X LIGHTSPEED using the original
LIGHTSPEED receiver:

- receiver VID/PID: `046D:C547`
- receiver subdevice/index: `01`
- device model ID: `409F`
- active firmware: `MPM 30.00.B0014`
- HID++ 2.0 ONBOARD_PROFILES feature: `0x8100`
- Memory Model 1
- Profile Format 3
- Macro Format 1
- 5 profiles
- 16 sectors
- 255 bytes per sector

Population size: **n=1**.

## Physically validated behavior

The development hardware tests exercised:

- direct Keyboard, Mouse and Consumer HID bindings;
- keyboard and Consumer Macro VM events;
- delay, wheel and horizontal-wheel Macro VM events;
- WAIT_FOR_RELEASE and REPEAT_WHILE_PRESSED;
- G-Shift normal/shifted layers;
- profile name, DPI and polling-rate fields;
- global macro pointers and cross-profile macro sharing;
- global macro deduplication;
- multi-page allocation;
- 5-byte cross-sector JUMP: `60 page_hi page_lo offset_hi offset_lo`;
- structurally valid raw/no-CRC Macro Format 1 page execution;
- compiler-managed macro pages with materialized CRC;
- interrupted-write reconciliation and fresh byte-for-byte readback.

The cross-page execution test manually placed a macro fragment in one page,
jumped to the next page and produced the expected keyboard result. A later
allocator-generated multi-page macro also executed correctly.

## Storage and recovery evidence

Profile/directory CRC is CRC16-CCITT with seed `0xFFFF`, calculated over bytes
0-252 and stored in bytes 253-254.

The compiler allocates macro data only in sectors 8-15. Sectors 6/7 remain
protected in this architecture, although externally authored Macro Format 1
chains may reference the wider post-profile area and can be followed read-only
by the validator.

## Read-only release smoke

The Windows read-only release smoke on the validated unit passed with:

- real C547 / subdevice 1 enumeration;
- no persistent mouse writes;
- exact-unit identity gate active with private identity redacted;
- compatible architecture / tested transport;
- full 16 x 255-byte read;
- structural validation PASS;
- Profiles 1, 2 and 3 enabled in the tested state;
- two referenced macro starts;
- no invalid live sectors;
- private checkpoint equality PASS;
- emitted shareable report privacy check PASS.

## Not locally validated

- a second physical G502 X;
- different firmware revisions;
- wired G502 X persistent writes;
- G502 X PLUS;
- Macro VM mouse press/release opcodes `0x41/0x42`;
- application-semantic integrations such as OBS/Discord/Overwolf.

Those remain read-only, external evidence, or unsupported until specifically
tested.
