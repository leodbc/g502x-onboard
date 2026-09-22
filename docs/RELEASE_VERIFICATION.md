# Release verification

Tagged releases are built from the clean public repository commit with
`tools/build_release.py`.

For version `0.1.0`, the canonical filenames are:

- `g502x-onboard-0.1.0.zip`
- `g502x-onboard-0.1.0.zip.sha256`

The extracted root is `g502x-onboard-0.1.0/`.

## Verify the checksum

Linux/macOS:

```bash
sha256sum -c g502x-onboard-0.1.0.zip.sha256
```

PowerShell:

```powershell
Get-FileHash .\g502x-onboard-0.1.0.zip -Algorithm SHA256
Get-Content .\g502x-onboard-0.1.0.zip.sha256
```

## Verify release structure and metadata

From a source checkout or extracted release containing the verifier:

```bash
python tools/verify_release.py g502x-onboard-0.1.0.zip
python tools/verify_release.py g502x-onboard-0.1.0
```

The verifier checks the exact file set, per-file SHA-256/size records,
source-commit binding, SPDX metadata, canonical ZIP layout, checksum sidecar
when present, path safety and the assertion that private state is absent.

## Reproducibility

The archive uses lexicographic ordering, stored/uncompressed entries, a fixed
1980-01-01 ZIP timestamp and mode 0644. Public CI builds the release on Linux
x64, Windows x64 and Windows x86 and requires the archive SHA-256 to match
across all three lanes before release.

## Attestation

Tagged public releases generate GitHub build provenance and SPDX SBOM
attestations, then publish the ZIP, checksum, SBOM and release manifest as
permanent GitHub Release assets. Release metadata is regenerated from the clean
public source commit and does not depend on separate development history.

Verify the build-provenance attestation after downloading the ZIP:

```bash
gh attestation verify g502x-onboard-0.1.0.zip -R leodbc/g502x-onboard
```

Verify the SPDX SBOM attestation:

```bash
gh attestation verify g502x-onboard-0.1.0.zip \
  -R leodbc/g502x-onboard \
  --predicate-type https://spdx.dev/Document/v2.3
```
