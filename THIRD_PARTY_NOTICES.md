# Third-party notices

The project is distributed under GPL-3.0-only and includes or depends on third-party components.

## omm.py

The HID++ transport under `libs/` is derived from **omm.py** by lexr1:

https://github.com/lexr1/omm.py

omm.py is distributed under GPL-3.0. See the repository root `LICENSE` and `NOTICE.md`.

## hidapi

The Windows bundle includes `hidapi.dll` binaries inherited byte-for-byte from
`lexr1/omm.py`. The release builder verifies their exact Git blob identities
before packaging. See `docs/PROVENANCE.md`.

The omm.py transport attributes those native binaries to:

https://github.com/libusb/hidapi

The inherited DLLs identify themselves as hidapi 0.15.0. Their exact SHA-256
values and Git-blob provenance are documented in
`docs/PROVENANCE.md`.

hidapi's BSD license is reproduced at:

`LICENSES/hidapi-BSD-3-Clause.txt`

## pyhidapi

The Python package installed as `hid` is provided by pyhidapi:

https://github.com/apmorton/pyhidapi

Its MIT license is reproduced at:

`LICENSES/pyhidapi-MIT.txt`

The Python runtime dependency surface is intentionally limited to the
hash-locked `hid`/pyhidapi binding. Legacy `all-escapes` codec usage from
omm.py was replaced by a small strict local `\\xNN` byte codec with exhaustive
round-trip tests.

This notice is attribution/provenance documentation, not legal advice.
