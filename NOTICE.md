# Notices and provenance

G502 X Onboard is an unofficial community project. It is not affiliated with,
endorsed by, or supported by Logitech.

## omm.py lineage

The low-level HID++ transport in `libs/` is derived from **omm.py** by lexr1:

https://github.com/lexr1/omm.py

Recorded upstream commit:

`8e6c3f00a20acbde1d3001244c9ca24db613804f`

The inherited/adapted Python transport remains under GPL-3.0. The runtime in
`g502x_onboard/` builds on that transport and is distributed under the same
GPL-3.0-only project license.

Files that are copied or materially derived from omm.py carry source-level
provenance comments as an additional attribution layer.

## Other protocol references

Protocol behavior was compared with established and independent open-source
Logitech tooling, including libratbag and Solaar. Those projects are references
and corroborating implementations; their code is not presented as original work
of this project.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[docs/PROVENANCE.md](docs/PROVENANCE.md) for dependency and binary provenance.
