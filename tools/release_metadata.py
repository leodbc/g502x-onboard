#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
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


PACKAGE_NAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
)
PACKAGE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
SHA256_TOKEN_RE = re.compile(r"^--hash=sha256:([0-9a-fA-F]{64})$")


def canonicalize_package_name(name: str) -> str:
    """Return the PyPA canonical project identity used by release metadata."""
    if not isinstance(name, str) or not PACKAGE_NAME_RE.fullmatch(name):
        raise ValueError(f"invalid dependency name: {name!r}")
    return re.sub(r"[-_.]+", "-", name).lower()


def spdx_pypi_id(name: str) -> str:
    return "SPDXRef-Package-pypi-" + canonicalize_package_name(name)


def parse_requirements_lock(text: str) -> dict[str, str]:
    """Parse the deliberately narrow hash-locked release requirements grammar."""
    logical = text.replace("\\\r\n", " ").replace("\\\n", " ")
    lines = [
        line.strip()
        for line in logical.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    require_hashes_count = sum(line == "--require-hashes" for line in lines)
    if require_hashes_count != 1:
        raise ValueError(
            "requirements lock must contain exactly one --require-hashes"
        )

    packages: dict[str, str] = {}
    for line in lines:
        if line == "--require-hashes":
            continue
        if line.startswith("--") or line.startswith("-"):
            raise ValueError(f"unsupported requirements option: {line}")

        parts = line.split()
        if not parts:
            continue
        pin = parts[0]
        if pin.count("==") != 1:
            raise ValueError(f"requirement is not exactly pinned: {line}")
        name, version = pin.split("==", 1)
        key = canonicalize_package_name(name)
        if not version or not PACKAGE_VERSION_RE.fullmatch(version):
            raise ValueError(f"invalid exact dependency version: {line}")

        hashes: list[str] = []
        for token in parts[1:]:
            if token.startswith("--hash=sha256:"):
                match = SHA256_TOKEN_RE.fullmatch(token)
                if match is None:
                    raise ValueError(
                        f"requirement pin has an invalid sha256 hash: {line}"
                    )
                hashes.append(match.group(1))
                continue
            if token.startswith("--hash="):
                raise ValueError(
                    f"requirement pin uses unsupported hash algorithm: {line}"
                )
            raise ValueError(
                f"requirement pin has unsupported trailing syntax: {line}"
            )

        if not hashes:
            raise ValueError(
                f"requirement pin is missing a sha256 hash: {line}"
            )

        if key in packages:
            raise ValueError(
                f"canonical package name collision/duplicate requirement: "
                f"{name!r} -> {key!r}"
            )
        packages[key] = version

    if not packages:
        raise ValueError("requirements lock contains no packages")
    return dict(sorted(packages.items()))


def spdx_document_semantics(*, version: str, source_commit: str) -> dict:
    """Return canonical static SPDX document semantics for a release."""
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
            "creators": ["Tool: g502x-release-metadata"],
        },
        "documentDescribes": ["SPDXRef-Package-g502x-onboard"],
    }


def spdx_root_package(*, version: str) -> dict:
    return {
        "SPDXID": "SPDXRef-Package-g502x-onboard",
        "name": "g502x-onboard",
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "GPL-3.0-only",
        "licenseDeclared": "GPL-3.0-only",
        "copyrightText": "NOASSERTION",
    }


def spdx_dependency(
    *,
    name: str,
    version: str,
    scope: str,
) -> dict:
    dependency_metadata = {
        "hid": {
            "license": "MIT",
            "download": "https://pypi.org/project/hid/",
        },
    }
    meta = dependency_metadata.get(
        name,
        {"license": "NOASSERTION", "download": "NOASSERTION"},
    )
    return {
        "SPDXID": spdx_pypi_id(name),
        "name": name,
        "versionInfo": version,
        "downloadLocation": meta["download"],
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": meta["license"],
        "copyrightText": "NOASSERTION",
        "externalRefs": [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{name}@{version}",
            }
        ],
        "comment": (
            "Core runtime dependency from requirements.txt."
            if scope == "core"
            else "Optional Textual UI runtime dependency from requirements-tui.txt."
        ),
    }


def spdx_vendored_hidapi_package() -> dict:
    return {
        "SPDXID": "SPDXRef-Package-vendored-hidapi",
        "name": "hidapi",
        "versionInfo": "0.15.0",
        "downloadLocation": (
            "https://github.com/libusb/hidapi/releases/tag/hidapi-0.15.0"
        ),
        "filesAnalyzed": False,
        "licenseConcluded": "BSD-3-Clause",
        "licenseDeclared": "BSD-3-Clause",
        "copyrightText": "NOASSERTION",
        "comment": (
            "Represents external/upstream hidapi 0.15.0 metadata. Windows "
            "DLLs shipped by this release are inherited byte-for-byte from "
            "the recorded lexr1/omm.py commit; exact inherited DLL SHA-256 "
            "values are documented and release-bound."
        ),
    }


def spdx_hidapi_file(*, arch: str, checksum: str) -> dict:
    return {
        "SPDXID": f"SPDXRef-File-hidapi-{arch}",
        "fileName": f"./libs/{arch}/hidapi.dll",
        "checksums": [
            {
                "algorithm": "SHA256",
                "checksumValue": checksum,
            }
        ],
        "licenseConcluded": "BSD-3-Clause",
        "licenseInfoInFiles": ["BSD-3-Clause"],
        "copyrightText": "NOASSERTION",
    }


def spdx_hidapi_provenance_relationship(*, arch: str) -> dict:
    return {
        "spdxElementId": f"SPDXRef-File-hidapi-{arch}",
        "relationshipType": "OTHER",
        "relatedSpdxElement": "SPDXRef-Package-vendored-hidapi",
        "comment": (
            f"Vendored {arch} hidapi.dll is inherited byte-for-byte from "
            "hidapi 0.15.0 through the recorded lexr1/omm.py provenance "
            "lineage."
        ),
    }


def build_spdx(
    *,
    version: str,
    source_commit: str,
    source_timestamp: str,
    requirements_text: str,
    optional_requirements_text: str | None = None,
    hidapi_files: Mapping[str, str | Path],
) -> dict:
    """Build a deterministic SPDX 2.3 dependency/vendor SBOM."""
    core_packages = parse_requirements_lock(requirements_text)
    optional_packages = (
        parse_requirements_lock(optional_requirements_text)
        if optional_requirements_text is not None
        else {}
    )
    overlap = set(core_packages) & set(optional_packages)
    if overlap:
        raise ValueError(
            "dependency cannot be both core and optional: "
            + ", ".join(sorted(overlap))
        )

    root_id = "SPDXRef-Package-g502x-onboard"
    spdx_packages = [spdx_root_package(version=version)]
    relationships = []

    for name, dep_version in core_packages.items():
        package = spdx_dependency(name=name, version=dep_version, scope="core")
        spdx_packages.append(package)
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package["SPDXID"],
            }
        )

    for name, dep_version in optional_packages.items():
        package = spdx_dependency(
            name=name,
            version=dep_version,
            scope="optional_tui",
        )
        spdx_packages.append(package)
        relationships.append(
            {
                "spdxElementId": package["SPDXID"],
                "relationshipType": "OPTIONAL_DEPENDENCY_OF",
                "relatedSpdxElement": root_id,
            }
        )

    hidapi_id = "SPDXRef-Package-vendored-hidapi"
    spdx_packages.append(spdx_vendored_hidapi_package())
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
        files.append(
            spdx_hidapi_file(
                arch=arch,
                checksum=sha256_file(path),
            )
        )
        relationships.append(
            spdx_hidapi_provenance_relationship(arch=arch)
        )

    document = spdx_document_semantics(
        version=version,
        source_commit=source_commit,
    )
    document["creationInfo"]["created"] = normalize_git_timestamp(
        source_timestamp
    )
    document.update(
        {
            "packages": spdx_packages,
            "files": files,
            "relationships": relationships,
        }
    )
    return document


def write_spdx(path: str | Path, **kwargs) -> Path:
    out = Path(path)
    payload = build_spdx(**kwargs)
    out.write_bytes(
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    return out
