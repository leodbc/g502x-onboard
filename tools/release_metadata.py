#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_git_timestamp(value: str) -> str:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_requirements_lock(text: str) -> dict[str, str]:
    logical = text.replace("\\\n", " ")
    lines = [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if "--require-hashes" not in lines:
        raise ValueError("requirements lock must enable --require-hashes")

    packages: dict[str, str] = {}
    for line in lines:
        if line.startswith("--"):
            continue
        parts = line.split()
        pin = parts[0]
        if pin.count("==") != 1:
            raise ValueError(f"requirement is not exactly pinned: {line}")
        name, version = pin.split("==", 1)
        if not name or not version:
            raise ValueError(f"empty dependency name/version: {line}")

        hash_tokens = [
            token for token in parts[1:] if token.startswith("--hash=")
        ]
        unsupported = [
            token for token in hash_tokens
            if not token.startswith("--hash=sha256:")
        ]
        if unsupported:
            raise ValueError(
                f"requirement pin uses unsupported hash algorithm: {line}"
            )

        hashes = [
            token.removeprefix("--hash=sha256:")
            for token in hash_tokens
        ]
        if not hashes:
            raise ValueError(
                f"requirement pin is missing a sha256 hash: {line}"
            )
        for digest in hashes:
            if (
                len(digest) != 64
                or any(ch not in "0123456789abcdefABCDEF" for ch in digest)
            ):
                raise ValueError(
                    f"requirement pin has an invalid sha256 hash: {line}"
                )
        packages[name.lower()] = version

    if not packages:
        raise ValueError("requirements lock contains no packages")
    return dict(sorted(packages.items()))


def build_spdx(
    *,
    version: str,
    source_commit: str,
    source_timestamp: str,
    requirements_text: str,
    hidapi_files: Mapping[str, str | Path],
) -> dict:
    """Build a deterministic SPDX 2.3 dependency/vendor SBOM."""
    packages = parse_requirements_lock(requirements_text)
    root_id = "SPDXRef-Package-g502x-onboard"

    spdx_packages = [
        {
            "SPDXID": root_id,
            "name": "g502x-onboard",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "GPL-3.0-only",
            "licenseDeclared": "GPL-3.0-only",
            "copyrightText": "NOASSERTION",
        }
    ]
    relationships = []

    dependency_metadata = {
        "hid": {
            "license": "MIT",
            "download": "https://pypi.org/project/hid/",
        },
    }

    for name, dep_version in packages.items():
        dep_id = "SPDXRef-Package-pypi-" + name.replace("_", "-")
        meta = dependency_metadata.get(
            name,
            {"license": "NOASSERTION", "download": "NOASSERTION"},
        )
        spdx_packages.append(
            {
                "SPDXID": dep_id,
                "name": name,
                "versionInfo": dep_version,
                "downloadLocation": meta["download"],
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": meta["license"],
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{name}@{dep_version}",
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": dep_id,
            }
        )

    hidapi_id = "SPDXRef-Package-vendored-hidapi"
    spdx_packages.append(
        {
            "SPDXID": hidapi_id,
            "name": "hidapi",
            "versionInfo": "0.15.0",
            "downloadLocation": "https://github.com/libusb/hidapi/releases/tag/hidapi-0.15.0",
            "filesAnalyzed": False,
            "licenseConcluded": "BSD-3-Clause",
            "licenseDeclared": "BSD-3-Clause",
            "copyrightText": "NOASSERTION",
            "comment": (
                "Windows DLLs are inherited byte-for-byte from the recorded "
                "lexr1/omm.py commit. Exact inherited DLL SHA-256 values are "
                "documented and release-bound."
            ),
        }
    )
    relationships.append(
        {
            "spdxElementId": root_id,
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": hidapi_id,
        }
    )

    files = []
    for arch, raw_path in sorted(hidapi_files.items()):
        path = Path(raw_path)
        file_id = f"SPDXRef-File-hidapi-{arch}"
        rel = f"./libs/{arch}/hidapi.dll"
        files.append(
            {
                "SPDXID": file_id,
                "fileName": rel,
                "checksums": [
                    {
                        "algorithm": "SHA256",
                        "checksumValue": sha256_file(path),
                    }
                ],
                "licenseConcluded": "BSD-3-Clause",
                "licenseInfoInFiles": ["BSD-3-Clause"],
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": hidapi_id,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": file_id,
            }
        )

    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"g502x-onboard-{version}",
        "documentNamespace": (
            "https://spdx.org/spdxdocs/"
            f"g502x-onboard-{source_commit}"
        ),
        "creationInfo": {
            "created": normalize_git_timestamp(source_timestamp),
            "creators": ["Tool: g502x-release-metadata"],
        },
        "documentDescribes": [root_id],
        "packages": spdx_packages,
        "files": files,
        "relationships": relationships,
    }


def write_spdx(path: str | Path, **kwargs) -> Path:
    out = Path(path)
    payload = build_spdx(**kwargs)
    out.write_bytes(
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return out
