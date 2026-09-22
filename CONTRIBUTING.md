# Contributing

Contributions are welcome, especially reproducible protocol evidence, sanitized
hardware reports, compiler/validator tests and narrowly scoped support for new
firmware or transports.

This project writes persistent onboard configuration, so support claims and
write authority stay conservative.

## Before opening a code change

Run:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python g502x.py selftest
python g502x.py --help
python g502x.py capabilities --json
```

Protocol-affecting changes should include:

- byte-level local evidence or an authoritative/corroborating external source;
- an offline regression test;
- the exact device / transport / Profile Format / Macro Format scope;
- no broader hardware claim than the evidence supports.

## Hardware reports

For a unit that is not already write-supported, start read-only:

```bash
python g502x.py report probe probe-report.json
python g502x.py report check probe-report.json
```

After a supported unit has been set up locally:

```bash
python g502x.py report device device-report.json --state
python g502x.py report check device-report.json
```

Do not attach raw `probe --json`, `debug export`, local baselines, raw sector
dumps, unit IDs, serial numbers, private fingerprints, or personal macro
contents.

## Safety

Do not weaken Profile 1 / sectors 6-7 recovery protection, exact-unit binding,
host-writer interlocks, or byte-for-byte readback merely to make a new device
pass.

Unknown devices, transports and firmware should remain read-only until there is
specific evidence for persistent writes.

## Provenance

If code is copied or adapted from another project, add attribution at the point
of reuse and update the relevant notice/provenance documentation.
