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

## Optional Textual UI dependency set

The optional v0.2.0 terminal UI dependency closure is separate from the core
runtime and is exact-pinned/hash-locked in `requirements-tui.txt`:

- `textual==8.2.8`
- `markdown-it-py==4.2.0`
- `mdit-py-plugins==0.6.1`
- `rich==15.0.0`
- `typing-extensions==4.16.0`
- `platformdirs==4.11.14`
- `pygments==2.21.0`
- `linkify-it-py==2.1.0`
- `uc-micro-py==2.0.0`
- `mdurl==0.1.2`

These packages are represented in the v0.2.0 SPDX SBOM as optional TUI
dependencies rather than mandatory core dependencies. Exact distribution hashes
remain authoritative in the committed lock file.

## Vendored Windows hidapi

The Windows DLLs are inherited byte-for-byte from the recorded omm.py commit
and are identified as hidapi 0.15.0.

| path | bytes | Git blob SHA-1 | SHA-256 |
| --- | ---: | --- | --- |
| `libs/x64/hidapi.dll` | 166912 | `01db12861eb3b70afc7256b7adb6c7c82e27849f` | `d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583` |
| `libs/x86/hidapi.dll` | 139776 | `c55b1608bf03aff3b4d24037213357b7187e0f94` | `d4c8e5f799fc5f57038d492f13a687471d5d353447ead7e6cae70e957fb55ae1` |

On Windows the loader checks architecture, expected bundled path, hidapi version
and SHA-256 before HID enumeration.

In the v0.2.0 SPDX model, `SPDXRef-Package-vendored-hidapi` represents
external/upstream hidapi 0.15.0 metadata rather than a package whose file
contents were analyzed by this project, so it remains `filesAnalyzed=false`.
The shipped x64/x86 DLLs are separate release File elements. Each file has an
`OTHER` relationship pointing to the external hidapi package, with an exact
relationship comment recording that the DLL is inherited byte-for-byte through
the recorded lexr1/omm.py provenance lineage. This deliberately avoids
`CONTAINS`, which would assert package membership, and `GENERATED_FROM`,
which would assert a build/generation fact not established by the recorded
provenance.

## Release metadata

Release builds generate:

- `RELEASE_MANIFEST.json` bound to the public source commit and, for v0.2.0,
  separately recording core and optional-TUI dependency locks;
- `SBOM.spdx.json` in SPDX 2.3 format with core, optional TUI, and vendored
  native dependency provenance;
- deterministic ZIP output;
- a SHA-256 sidecar;
- GitHub build/SBOM attestations on tagged public releases.

See [RELEASE_VERIFICATION.md](RELEASE_VERIFICATION.md).
