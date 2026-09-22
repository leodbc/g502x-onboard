## Scope

Describe the exact device / transport / Profile Format / Macro Format affected.

## Evidence

- [ ] **Hardware validated locally**
- [ ] **Corroborated by external implementations/sources**
- [ ] **Observed negative result**
- [ ] **Hypothesis / not yet validated**

Evidence / test-vector reference:

<!-- Do not paste private device identifiers or user-specific raw dumps. -->

## Safety checklist

- [ ] I did not weaken Profile 1 / sectors 6–7 recovery protection.
- [ ] I did not broaden persistent-write support beyond hardware evidence without keeping the new path read-only.
- [ ] Persistent-write changes preserve exact-unit identity checks and byte-for-byte readback/reconciliation.
- [ ] Unknown bytes are preserved rather than synthesized unless their semantics are proven.
- [ ] No unit ID, serial number, private fingerprint, local baseline, raw personal macro dump, or G HUB account/database data is included.
- [ ] Offline regression tests cover the changed protocol/compiler behavior.
- [ ] Documentation/support claims are limited to the device/format actually evidenced.

## Validation

```text
python g502x.py selftest
python -m unittest discover -s tests -p "test_*.py" -v
```

Hardware validation, if any:

<!-- State device, transport, read-only/write scope, and result. -->
