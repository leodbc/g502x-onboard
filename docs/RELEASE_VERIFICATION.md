# Release verification

Tagged releases are built from the clean public repository commit with
`tools/build_release.py`.

For version `0.2.0`, the canonical filenames are:

- `g502x-onboard-0.2.0.zip`
- `g502x-onboard-0.2.0.zip.sha256`

The extracted root is `g502x-onboard-0.2.0/`.

The release contains both dependency inputs:

- `requirements.txt` — mandatory core runtime lock;
- `requirements-tui.txt` — optional Textual UI runtime lock.

Both inputs enable pip hash checking, use exact pins, and are recorded
separately in the release manifest and SPDX SBOM. Textual/Rich remain optional
and are not part of the core dependency set.

## Verify the checksum

Linux/macOS:

```bash
sha256sum -c g502x-onboard-0.2.0.zip.sha256
```

PowerShell:

```powershell
Get-FileHash .\g502x-onboard-0.2.0.zip -Algorithm SHA256
Get-Content .\g502x-onboard-0.2.0.zip.sha256
```

## Verify release structure and metadata

From a source checkout or extracted release containing the verifier:

```bash
python tools/verify_release.py g502x-onboard-0.2.0.zip
python tools/verify_release.py g502x-onboard-0.2.0
```

The v0.2 verifier checks the exact file set, per-file SHA-256/size records,
source-commit binding, package/release version agreement, both dependency-lock
digests and package sets, SPDX dependency scope, canonical ZIP layout, checksum
sidecar when present, path safety, and the assertion that private state is
absent.

The verifier continues to accept the historical v1 manifest format used by
v0.1.0; this compatibility does not modify the immutable v0.1.0 release.

## Extracted-artifact smoke

Phase 6 CI validates the actual extracted release in fresh virtual environments
created only from lock files shipped inside that release.

Core-only/no-Textual:

```bash
python tools/extracted_release_smoke.py g502x-onboard-0.2.0 --mode core --fresh-env
```

This installs only `requirements.txt`, verifies Textual/Rich are absent, then
runs the extracted CLI version, selftest and capabilities paths. It also checks
that TUI help remains available and that attempting to launch the TUI fails
cleanly with optional-install guidance before hardware access.

Locked TUI:

```bash
python tools/extracted_release_smoke.py g502x-onboard-0.2.0 --mode tui --fresh-env
```

This installs `requirements.txt` plus the extracted
`requirements-tui.txt` in a fresh environment and proves the packaged TUI
entry point and Textual application class load from the extracted artifact.
These smokes are software-only and perform no physical device operation.

## Reproducibility

The archive uses lexicographic POSIX-relative ordering, stored/uncompressed
entries, a fixed 1980-01-01 ZIP timestamp and mode 0644. Public CI builds the
release on Linux x64, Windows x64 and Windows x86 and requires the archive
SHA-256 to match across all three lanes.

## Version and tag binding

The product version is read from `g502x_onboard/__init__.py`. The release
builder rejects a tag that is not exactly `v<product-version>`. For v0.2.0,
the generated manifest also records `version=0.2.0`,
`release_name=g502x-onboard-0.2.0`, both entry points, both dependency inputs,
and the exact source commit.

The historical `v0.1.0` tag/release remains immutable.

## Attestation and publication

The tag-bound public release workflow verifies the complete release before
attestation, then generates GitHub build provenance and SPDX SBOM attestations
and publishes the ZIP, checksum, SBOM and release manifest as permanent GitHub
Release assets.

The committed `docs/RELEASE_NOTES_V0.2.0.md` is the prepared release-note
source for v0.2.0. Its presence does not mean the tag or GitHub Release has
already been published.

After downloading a published ZIP, verify build provenance with:

```bash
gh attestation verify g502x-onboard-0.2.0.zip -R leodbc/g502x-onboard
```

Verify the SPDX SBOM attestation with:

```bash
gh attestation verify g502x-onboard-0.2.0.zip \
  -R leodbc/g502x-onboard \
  --predicate-type https://spdx.dev/Document/v2.3
```
