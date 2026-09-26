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


def _spdx_dependency(
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
    package = {
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
    return package


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

    for name, dep_version in core_packages.items():
        package = _spdx_dependency(name=name, version=dep_version, scope="core")
        spdx_packages.append(package)
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package["SPDXID"],
            }
        )

    for name, dep_version in optional_packages.items():
        package = _spdx_dependency(name=name, version=dep_version, scope="optional_tui")
        spdx_packages.append(package)
        relationships.append(
            {
                "spdxElementId": package["SPDXID"],
                "relationshipType": "OPTIONAL_DEPENDENCY_OF",
                "relatedSpdxElement": root_id,
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
