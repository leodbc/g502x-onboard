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
digests and package sets, the exact SPDX package/file/relationship model plus
canonical `documentDescribes`, canonical ZIP layout, checksum sidecar when
present, path safety, and the assertion that private state is absent.

For the v0.2 SPDX document, verification also enforces the canonical semantic
claims emitted by the release builder: SPDX 2.3 / CC0-1.0 document identity,
the exact release name and source-bound namespace, the release-metadata tool
creator, root-package download/license/analyzed-file semantics, core and
optional dependency download/license/purl/scope provenance, vendored hidapi
version/download/license/provenance, and vendored DLL license rows. These
checks are semantic field checks; the verifier does not require byte-for-byte
SBOM regeneration.

The vendored hidapi package is modeled as an external/upstream hidapi 0.15.0
metadata reference with `filesAnalyzed=false`. The two shipped DLLs remain
separate SPDX File elements. Each DLL points to that external package with an
`OTHER` relationship and a canonical relationship comment recording the
byte-for-byte provenance through the recorded lexr1/omm.py lineage. The model
intentionally does not use `CONTAINS` or `GENERATED_FROM`: the repository
evidence establishes provenance, but does not establish that the external
metadata package contains the release files or that this project generated
those DLLs.

For every SPDX package row with `filesAnalyzed=false`, the verifier also
rejects `packageVerificationCode` and `licenseInfoFromFiles`, because SPDX
2.3 requires those conditional fields to be omitted in the non-analyzed
package state.

`creationInfo.created` is required to be a valid normalized UTC timestamp at
second resolution in the builder's canonical `YYYY-MM-DDTHH:MM:SSZ` form.
The release manifest does not independently authenticate the Git commit
timestamp, so verification does **not** claim that this field equals Git commit
time; it validates presence, timestamp validity, UTC normalization, and
canonical representation only.

The verifier continues to accept the historical v1 manifest format only for
the immutable v0.1.0 contract. In addition to version 0.1.0, the recorded
source commit, historical CLI entry point/dependency shape, and canonical
archive root, the raw `RELEASE_MANIFEST.json` bytes must match the immutable
published v0.1.0 manifest asset SHA-256:

`79eaa918186b1b084331c2c66c5dd188d5d20eeb5a9db70b9e32dbf4e1659692`

That published manifest fixes the historical file set, per-file sizes and
SHA-256 values, plus the SBOM digest; the verifier then rechecks those declared
bytes against the supplied payload. This makes v1 compatibility content-bound
rather than a generic weaker format for newly constructed artifacts. The
digest is frozen from immutable GitHub Release asset 582484050; the same
release records the canonical ZIP SHA-256
`933d4446d612a8c63d00b3c08d2ffd45631504f9b61999075efe137d4e68919d`.
Verification remains deterministic/offline and does not modify or query the
historical release at runtime.

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
