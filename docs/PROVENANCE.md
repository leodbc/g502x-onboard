# Provenance

## Project license

G502 X Onboard is distributed under GPL-3.0-only.

## omm.py transport lineage

The low-level HID++ transport under `libs/` originates from or is adapted from
`lexr1/omm.py`:

https://github.com/lexr1/omm.py

Recorded upstream commit:

`8e6c3f00a20acbde1d3001244c9ca24db613804f`

`libs/HidppFeatures.py` is inherited directly from that lineage.
`libs/LogiHPP20.py` and `libs/utils.py` contain project-specific changes.
Source-level comments identify that provenance in the inherited files.

## Python dependency

The runtime Python dependency is hash-locked in `requirements.txt`:

- `hid==1.0.9` (pyhidapi binding)

The release requires pip hash checking.

## Vendored Windows hidapi

The Windows DLLs are inherited byte-for-byte from the recorded omm.py commit
and are identified as hidapi 0.15.0.

| path | bytes | Git blob SHA-1 | SHA-256 |
| --- | ---: | --- | --- |
| `libs/x64/hidapi.dll` | 166912 | `01db12861eb3b70afc7256b7adb6c7c82e27849f` | `d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583` |
| `libs/x86/hidapi.dll` | 139776 | `c55b1608bf03aff3b4d24037213357b7187e0f94` | `d4c8e5f799fc5f57038d492f13a687471d5d353447ead7e6cae70e957fb55ae1` |

On Windows the loader checks architecture, expected bundled path, hidapi version
and SHA-256 before HID enumeration.

## Release metadata

Release builds generate:

- `RELEASE_MANIFEST.json` bound to the public source commit;
- `SBOM.spdx.json` in SPDX 2.3 format;
- deterministic ZIP output;
- a SHA-256 sidecar;
- GitHub build/SBOM attestations on tagged public releases.

See [RELEASE_VERIFICATION.md](RELEASE_VERIFICATION.md).
