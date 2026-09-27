#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from release_metadata import (
    canonicalize_package_name,
    parse_requirements_lock,
    spdx_pypi_id,
)


SUPPORTED_FORMATS = {"g502x-release-v1", "g502x-release-v2"}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 2048
CANONICAL_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
CANONICAL_FILE_MODE = 0o100644
HISTORICAL_V1_VERSION = "0.1.0"
HISTORICAL_V1_SOURCE_COMMIT = "bf3d4458176b0c8326888158d08d3332c1429737"
HISTORICAL_V1_RELEASE_NAME = "g502x-onboard-0.1.0"
HISTORICAL_V1_ENTRYPOINT = "g502x.py"
HISTORICAL_V1_PACKAGES = {"hid": "1.0.9"}
# Immutable GitHub Release asset digest for v0.1.0 RELEASE_MANIFEST.json.
# Release asset 582484050, published from the tag-bound v0.1.0 release workflow.
HISTORICAL_V1_MANIFEST_SHA256 = (
    "79eaa918186b1b084331c2c66c5dd188d5d20eeb5a9db70b9e32dbf4e1659692"
)
REQUIRED_V2_FILES = {
    "g502x.py",
    "g502x_tui.py",
    "requirements.txt",
    "requirements-tui.txt",
    "g502x_onboard/__init__.py",
    "libs/utils.py",
    "libs/x64/hidapi.dll",
    "libs/x86/hidapi.dll",
    "g502x_onboard/tui/app.py",
    "g502x_onboard/tui/bootstrap.py",
    "g502x_onboard/tui/runner.py",
    "docs/RELEASE_NOTES_V0.2.0.md",
    "tools/extracted_release_smoke.py",
    "tools/verify_release.py",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_name(name: str, *, context: str) -> str:
    if not isinstance(name, str) or not name:
        raise RuntimeError(f"{context}: empty/non-string path")
    if "\\" in name or "\x00" in name:
        raise RuntimeError(f"{context}: non-canonical path separator/name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError(f"{context}: unsafe path: {name!r}")
    normalized = path.as_posix()
    if normalized != name or normalized in ("", "."):
        raise RuntimeError(f"{context}: non-canonical path: {name!r}")
    return normalized


def _load_json_bytes(data: bytes, *, context: str) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"{context}: invalid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{context}: JSON root must be an object")
    return value



def _verify_historical_v1_manifest_digest(digest: str) -> None:
    if (
        not isinstance(digest, str)
        or digest.lower() != HISTORICAL_V1_MANIFEST_SHA256
    ):
        raise RuntimeError("historical v1 manifest content digest mismatch")


def _verify_historical_v1_manifest_content(manifest_bytes: bytes) -> None:
    _verify_historical_v1_manifest_digest(sha256_bytes(manifest_bytes))


def _verify_historical_v1_contract(
    manifest: dict,
) -> tuple[dict[str, str], dict[str, str], str]:
    if manifest.get("version") != HISTORICAL_V1_VERSION:
        raise RuntimeError("historical v1 manifest version mismatch")
    if manifest.get("source_commit") != HISTORICAL_V1_SOURCE_COMMIT:
        raise RuntimeError("historical v1 manifest source_commit mismatch")
    if manifest.get("entrypoint") != HISTORICAL_V1_ENTRYPOINT:
        raise RuntimeError("historical v1 manifest entrypoint mismatch")

    requirements = manifest.get("python_requirements")
    if (
        not isinstance(requirements, dict)
        or requirements.get("hash_checking") is not True
        or requirements.get("packages") != HISTORICAL_V1_PACKAGES
    ):
        raise RuntimeError(
            "historical v1 manifest dependency contract mismatch"
        )
    return dict(HISTORICAL_V1_PACKAGES), {}, HISTORICAL_V1_VERSION


def _verify_historical_v1_sbom(sbom_json: dict) -> None:
    if sbom_json.get("name") != HISTORICAL_V1_RELEASE_NAME:
        raise RuntimeError("historical v1 SBOM release name mismatch")
    packages = sbom_json.get("packages")
    if not isinstance(packages, list):
        raise RuntimeError("historical v1 SBOM packages table missing")
    roots = [
        row for row in packages
        if isinstance(row, dict)
        and row.get("SPDXID") == "SPDXRef-Package-g502x-onboard"
    ]
    if len(roots) != 1 or roots[0].get("versionInfo") != HISTORICAL_V1_VERSION:
        raise RuntimeError("historical v1 SBOM root package mismatch")


def _runtime_hidapi_hashes(
    read_bytes: Callable[[str], bytes],
) -> dict[str, str]:
    try:
        source = read_bytes("libs/utils.py").decode("utf-8")
        tree = ast.parse(source, filename="libs/utils.py")
    except Exception as exc:
        raise RuntimeError("unable to inspect runtime hidapi hash authority") from exc

    value = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name)
            and target.id == "WINDOWS_HIDAPI_SHA256"
            for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except Exception as exc:
                raise RuntimeError(
                    "runtime hidapi hash authority is not a literal mapping"
                ) from exc
            break

    if not isinstance(value, dict) or set(value) != {"x64", "x86"}:
        raise RuntimeError("runtime hidapi hash authority is incomplete")

    out: dict[str, str] = {}
    for arch, digest in value.items():
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise RuntimeError(
                f"runtime hidapi hash authority invalid for {arch}"
            )
        out[arch] = digest.lower()
    return out


def _verify_v2_requirements(
    requirements: object,
    *,
    expected_files: set[str],
    read_bytes: Callable[[str], bytes],
) -> tuple[dict[str, str], dict[str, str]]:
    if not isinstance(requirements, dict):
        raise RuntimeError("release manifest python_requirements missing")
    parsed: dict[str, dict[str, str]] = {}
    for scope, expected_path in (
        ("core", "requirements.txt"),
        ("optional_tui", "requirements-tui.txt"),
    ):
        row = requirements.get(scope)
        if not isinstance(row, dict):
            raise RuntimeError(f"release manifest {scope} requirements missing")
        if row.get("hash_checking") is not True:
            raise RuntimeError(f"release manifest {scope} lock is not hash checked")
        path = row.get("path")
        if path != expected_path or path not in expected_files:
            raise RuntimeError(f"release manifest {scope} lock path mismatch")
        expected_hash = row.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise RuntimeError(f"release manifest {scope} lock sha256 invalid")
        data = read_bytes(path)
        if sha256_bytes(data).lower() != expected_hash.lower():
            raise RuntimeError(f"release manifest {scope} lock digest mismatch")
        try:
            lock_packages = parse_requirements_lock(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"release manifest {scope} lock invalid") from exc
        declared = row.get("packages")
        if declared != lock_packages:
            raise RuntimeError(f"release manifest {scope} package set mismatch")
        parsed[scope] = lock_packages

    overlap = set(parsed["core"]) & set(parsed["optional_tui"])
    if overlap:
        raise RuntimeError("core and optional dependency scopes overlap")
    return parsed["core"], parsed["optional_tui"]


def _verify_v2_contract(
    *,
    manifest: dict,
    expected_files: set[str],
    read_bytes: Callable[[str], bytes],
) -> tuple[dict[str, str], dict[str, str]]:
    missing = sorted(REQUIRED_V2_FILES - expected_files)
    if missing:
        raise RuntimeError(f"v2 release missing required TUI/release files: {missing}")

    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("v2 release manifest version missing")
    if manifest.get("release_name") != f"g502x-onboard-{version}":
        raise RuntimeError("v2 release_name/version mismatch")
    if manifest.get("entrypoints") != {"cli": "g502x.py", "tui": "g502x_tui.py"}:
        raise RuntimeError("v2 release entrypoints mismatch")

    init_text = read_bytes("g502x_onboard/__init__.py").decode("utf-8")
    match = VERSION_RE.search(init_text)
    if not match or match.group(1) != version:
        raise RuntimeError("v2 package version does not match release manifest")

    return _verify_v2_requirements(
        manifest.get("python_requirements"),
        expected_files=expected_files,
        read_bytes=read_bytes,
    )


def _verify_spdx_dependencies(
    *,
    sbom_json: dict,
    version: str,
    core_packages: dict[str, str],
    optional_packages: dict[str, str],
    read_bytes: Callable[[str], bytes],
) -> None:
    packages = sbom_json.get("packages")
    files = sbom_json.get("files")
    relationships = sbom_json.get("relationships")
    if not isinstance(packages, list):
        raise RuntimeError("SBOM packages table missing")
    if not isinstance(files, list):
        raise RuntimeError("SBOM files table missing")
    if not isinstance(relationships, list):
        raise RuntimeError("SBOM relationships table missing")

    document_id = sbom_json.get("SPDXID")
    if not isinstance(document_id, str):
        raise RuntimeError("SBOM document SPDXID missing")

    package_rows: dict[str, dict] = {}
    canonical_package_names: dict[str, str] = {}
    for row in packages:
        if not isinstance(row, dict) or not isinstance(row.get("SPDXID"), str):
            raise RuntimeError("SBOM package has invalid SPDXID")
        spdx_id = row["SPDXID"]
        if spdx_id in package_rows:
            raise RuntimeError(f"SBOM duplicate SPDXID: {spdx_id}")
        name = row.get("name")
        if not isinstance(name, str):
            raise RuntimeError(f"SBOM package name missing: {spdx_id}")
        try:
            canonical = canonicalize_package_name(name)
        except ValueError as exc:
            raise RuntimeError(f"SBOM package name invalid: {name!r}") from exc
        previous = canonical_package_names.get(canonical)
        if previous is not None:
            raise RuntimeError(
                "SBOM canonical package-name collision: "
                f"{canonical!r} ({previous}, {spdx_id})"
            )
        canonical_package_names[canonical] = spdx_id
        package_rows[spdx_id] = row

    file_rows: dict[str, dict] = {}
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("SPDXID"), str):
            raise RuntimeError("SBOM file has invalid SPDXID")
        spdx_id = row["SPDXID"]
        if spdx_id in file_rows or spdx_id in package_rows or spdx_id == document_id:
            raise RuntimeError(f"SBOM duplicate/colliding SPDXID: {spdx_id}")
        file_rows[spdx_id] = row
    if document_id in package_rows or document_id in file_rows:
        raise RuntimeError(f"SBOM duplicate/colliding SPDXID: {document_id}")

    all_ids = {document_id, *package_rows, *file_rows}
    relationship_set: set[tuple[str, str, str]] = set()
    for row in relationships:
        if not isinstance(row, dict):
            raise RuntimeError("SBOM relationship row invalid")
        left = row.get("spdxElementId")
        relation = row.get("relationshipType")
        right = row.get("relatedSpdxElement")
        if not all(isinstance(value, str) for value in (left, relation, right)):
            raise RuntimeError("SBOM relationship fields invalid")
        if left not in all_ids or right not in all_ids:
            raise RuntimeError("SBOM relationship references unknown SPDXID")
        triple = (left, relation, right)
        if triple in relationship_set:
            raise RuntimeError("SBOM duplicate relationship")
        relationship_set.add(triple)

    root_id = "SPDXRef-Package-g502x-onboard"
    root = package_rows.get(root_id)
    if (
        not isinstance(root, dict)
        or root.get("name") != "g502x-onboard"
        or root.get("versionInfo") != version
    ):
        raise RuntimeError("SBOM root package/version mismatch")
    if sbom_json.get("documentDescribes") != [root_id]:
        raise RuntimeError("SBOM documentDescribes mismatch")

    for name, dep_version in core_packages.items():
        dep_id = spdx_pypi_id(name)
        row = package_rows.get(dep_id)
        if (
            not isinstance(row, dict)
            or canonicalize_package_name(row.get("name", "")) != name
            or row.get("versionInfo") != dep_version
        ):
            raise RuntimeError(f"SBOM core dependency mismatch: {name}")
        if (root_id, "DEPENDS_ON", dep_id) not in relationship_set:
            raise RuntimeError(f"SBOM core dependency relationship missing: {name}")

    for name, dep_version in optional_packages.items():
        dep_id = spdx_pypi_id(name)
        row = package_rows.get(dep_id)
        if (
            not isinstance(row, dict)
            or canonicalize_package_name(row.get("name", "")) != name
            or row.get("versionInfo") != dep_version
        ):
            raise RuntimeError(f"SBOM optional TUI dependency mismatch: {name}")
        if (dep_id, "OPTIONAL_DEPENDENCY_OF", root_id) not in relationship_set:
            raise RuntimeError(
                f"SBOM optional TUI dependency relationship missing: {name}"
            )

    hidapi_id = "SPDXRef-Package-vendored-hidapi"
    hidapi = package_rows.get(hidapi_id)
    if (
        not isinstance(hidapi, dict)
        or hidapi.get("name") != "hidapi"
        or hidapi.get("versionInfo") != "0.15.0"
    ):
        raise RuntimeError("SBOM vendored hidapi package/version mismatch")
    if (root_id, "DEPENDS_ON", hidapi_id) not in relationship_set:
        raise RuntimeError("SBOM vendored hidapi dependency relationship missing")

    runtime_hashes = _runtime_hidapi_hashes(read_bytes)
    for arch in ("x64", "x86"):
        rel = f"libs/{arch}/hidapi.dll"
        actual_hash = sha256_bytes(read_bytes(rel)).lower()
        if actual_hash != runtime_hashes[arch]:
            raise RuntimeError(
                f"vendored hidapi {arch} bytes disagree with runtime hash authority"
            )

        file_id = f"SPDXRef-File-hidapi-{arch}"
        row = file_rows.get(file_id)
        if not isinstance(row, dict) or row.get("fileName") != f"./{rel}":
            raise RuntimeError(f"SBOM vendored hidapi file record mismatch: {arch}")
        checksums = row.get("checksums")
        if not isinstance(checksums, list):
            raise RuntimeError(f"SBOM vendored hidapi checksum missing: {arch}")
        sha256_values = {
            item.get("checksumValue", "").lower()
            for item in checksums
            if isinstance(item, dict) and item.get("algorithm") == "SHA256"
            and isinstance(item.get("checksumValue"), str)
        }
        if sha256_values != {actual_hash}:
            raise RuntimeError(f"SBOM vendored hidapi SHA-256 mismatch: {arch}")
        if (hidapi_id, "CONTAINS", file_id) not in relationship_set:
            raise RuntimeError(
                f"SBOM vendored hidapi containment relationship missing: {arch}"
            )

    expected_package_ids = {root_id, hidapi_id}
    expected_package_ids.update(
        spdx_pypi_id(name) for name in core_packages
    )
    expected_package_ids.update(
        spdx_pypi_id(name) for name in optional_packages
    )
    observed_package_ids = set(package_rows)
    if observed_package_ids != expected_package_ids:
        raise RuntimeError(
            "SBOM package ID set mismatch; "
            f"missing={sorted(expected_package_ids - observed_package_ids)} "
            f"extra={sorted(observed_package_ids - expected_package_ids)}"
        )

    expected_file_ids = {
        "SPDXRef-File-hidapi-x64",
        "SPDXRef-File-hidapi-x86",
    }
    observed_file_ids = set(file_rows)
    if observed_file_ids != expected_file_ids:
        raise RuntimeError(
            "SBOM file ID set mismatch; "
            f"missing={sorted(expected_file_ids - observed_file_ids)} "
            f"extra={sorted(observed_file_ids - expected_file_ids)}"
        )

    expected_relationships = {
        (root_id, "DEPENDS_ON", spdx_pypi_id(name))
        for name in core_packages
    }
    expected_relationships.update(
        (spdx_pypi_id(name), "OPTIONAL_DEPENDENCY_OF", root_id)
        for name in optional_packages
    )
    expected_relationships.update(
        {
            (root_id, "DEPENDS_ON", hidapi_id),
            (hidapi_id, "CONTAINS", "SPDXRef-File-hidapi-x64"),
            (hidapi_id, "CONTAINS", "SPDXRef-File-hidapi-x86"),
        }
    )
    if relationship_set != expected_relationships:
        raise RuntimeError(
            "SBOM relationship set mismatch; "
            f"missing={sorted(expected_relationships - relationship_set)} "
            f"extra={sorted(relationship_set - expected_relationships)}"
        )


def _verify_payload(
    *,
    manifest: dict,
    manifest_bytes: bytes,
    actual_files: set[str],
    read_bytes: Callable[[str], bytes],
) -> None:
    fmt = manifest.get("format")
    if fmt not in SUPPORTED_FORMATS:
        raise RuntimeError(f"unsupported release manifest format: {fmt!r}")
    if manifest.get("private_state_included") is not False:
        raise RuntimeError("release manifest does not assert private_state_included=false")

    source_commit = manifest.get("source_commit")
    if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
        raise RuntimeError("release manifest has invalid source_commit")

    if manifest.get("archive_reproducible") is not True:
        raise RuntimeError("release manifest does not assert archive_reproducible=true")
    layout = manifest.get("archive_layout")
    expected_layout = {
        "order": "lexicographic",
        "timestamp": "1980-01-01T00:00:00",
        "compression": "stored",
        "file_mode": "0644",
    }
    if layout != expected_layout:
        raise RuntimeError("release manifest archive_layout is not canonical")

    rows = manifest.get("files")
    if not isinstance(rows, dict):
        raise RuntimeError("release manifest files table missing")

    expected_files = {
        _safe_relative_name(rel, context="release manifest file")
        for rel in rows
    }
    if len(expected_files) != len(rows):
        raise RuntimeError("release manifest contains duplicate canonical paths")
    folded = [rel.casefold() for rel in expected_files]
    if len(folded) != len(set(folded)):
        raise RuntimeError("release manifest contains case-colliding paths")
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise RuntimeError(
            f"release file set mismatch; missing={missing} extra={extra}"
        )

    for rel in sorted(expected_files):
        row = rows.get(rel)
        if not isinstance(row, dict):
            raise RuntimeError(f"manifest row is not an object: {rel}")
        expected_hash = row.get("sha256")
        expected_size = row.get("bytes")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            raise RuntimeError(f"manifest has invalid sha256 for {rel}")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise RuntimeError(f"manifest has invalid byte size for {rel}")

        data = read_bytes(rel)
        if len(data) != expected_size:
            raise RuntimeError(
                f"size mismatch for {rel}: {len(data)} != {expected_size}"
            )
        actual_hash = sha256_bytes(data)
        if actual_hash.lower() != expected_hash.lower():
            raise RuntimeError(
                f"sha256 mismatch for {rel}: {actual_hash} != {expected_hash}"
            )

    if fmt == "g502x-release-v1":
        core_packages, optional_packages, version = _verify_historical_v1_contract(
            manifest
        )
        _verify_historical_v1_manifest_content(manifest_bytes)
    else:
        core_packages, optional_packages = _verify_v2_contract(
            manifest=manifest,
            expected_files=expected_files,
            read_bytes=read_bytes,
        )
        version = manifest["version"]

    sbom = manifest.get("sbom")
    if not isinstance(sbom, dict):
        raise RuntimeError("release manifest SBOM metadata missing")
    sbom_path = sbom.get("path")
    sbom_hash = sbom.get("sha256")
    if not isinstance(sbom_path, str) or sbom_path not in expected_files:
        raise RuntimeError("release manifest has invalid SBOM path")
    if not isinstance(sbom_hash, str) or not SHA256_RE.fullmatch(sbom_hash):
        raise RuntimeError("release manifest has invalid SBOM sha256")

    sbom_bytes = read_bytes(sbom_path)
    if sha256_bytes(sbom_bytes).lower() != sbom_hash.lower():
        raise RuntimeError("SBOM digest does not match release manifest")
    sbom_json = _load_json_bytes(sbom_bytes, context="SBOM")
    if sbom_json.get("spdxVersion") != "SPDX-2.3":
        raise RuntimeError("SBOM is not SPDX-2.3")
    namespace = sbom_json.get("documentNamespace")
    if not isinstance(namespace, str) or not namespace.endswith(source_commit):
        raise RuntimeError("SBOM namespace is not bound to source_commit")

    if fmt == "g502x-release-v1":
        _verify_historical_v1_sbom(sbom_json)
    else:
        _verify_spdx_dependencies(
            sbom_json=sbom_json,
            version=version,
            core_packages=core_packages,
            optional_packages=optional_packages,
            read_bytes=read_bytes,
        )


def verify_directory(root: str | Path) -> None:
    supplied = Path(root)
    if supplied.is_symlink():
        raise RuntimeError(f"release directory symlink is not accepted: {supplied}")
    root = supplied.resolve()
    if not root.is_dir():
        raise RuntimeError(f"release directory not found: {root}")

    manifest_path = root / "RELEASE_MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("RELEASE_MANIFEST.json must be a regular file")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _load_json_bytes(
        manifest_bytes,
        context="release manifest",
    )

    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"release contains symlink: {path.relative_to(root)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError(f"release contains non-regular file: {path.relative_to(root)}")
        rel = path.relative_to(root).as_posix()
        if rel == "RELEASE_MANIFEST.json":
            continue
        files[rel] = path

    _verify_payload(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        actual_files=set(files),
        read_bytes=lambda rel: files[rel].read_bytes(),
    )


def _safe_archive_name(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise RuntimeError(f"unsafe ZIP member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError(f"unsafe ZIP member path: {name}")
    if path.as_posix() != name:
        raise RuntimeError(f"non-canonical ZIP member path: {name}")
    if len(path.parts) < 2:
        raise RuntimeError(f"ZIP member lacks one release root directory: {name}")
    return path


def verify_archive(archive: str | Path) -> None:
    archive = Path(archive).resolve()
    if not archive.is_file():
        raise RuntimeError(f"release archive not found: {archive}")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError(
            f"release archive exceeds {MAX_ARCHIVE_BYTES} byte safety limit"
        )

    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if sidecar.exists():
        if sidecar.is_symlink() or not sidecar.is_file():
            raise RuntimeError("checksum sidecar must be a regular file")
        if sidecar.stat().st_size > 1024:
            raise RuntimeError("checksum sidecar is unexpectedly large")
        line = sidecar.read_text(encoding="ascii").strip()
        parts = line.split()
        if len(parts) != 2:
            raise RuntimeError("checksum sidecar must contain '<sha256> <filename>'")
        expected_hash, expected_name = parts
        if not SHA256_RE.fullmatch(expected_hash):
            raise RuntimeError("checksum sidecar contains invalid sha256")
        if expected_name != archive.name:
            raise RuntimeError(
                f"checksum sidecar filename mismatch: {expected_name} != {archive.name}"
            )
        actual_archive_hash = sha256_file(archive)
        if actual_archive_hash.lower() != expected_hash.lower():
            raise RuntimeError("archive SHA-256 does not match checksum sidecar")

    with zipfile.ZipFile(archive, "r") as zf:
        all_infos = zf.infolist()
        if len(all_infos) > MAX_MEMBERS:
            raise RuntimeError("ZIP contains too many members")
        if any(info.is_dir() for info in all_infos):
            raise RuntimeError("canonical release ZIP must not contain directory entries")

        total_uncompressed = 0
        for info in all_infos:
            if info.flag_bits & 0x1:
                raise RuntimeError(f"ZIP contains encrypted member: {info.filename}")
            if info.compress_type != zipfile.ZIP_STORED:
                raise RuntimeError(
                    f"ZIP member is not stored/uncompressed: {info.filename}"
                )
            if info.date_time != CANONICAL_ZIP_TIMESTAMP:
                raise RuntimeError(
                    f"ZIP member has non-canonical timestamp: {info.filename}"
                )
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode != CANONICAL_FILE_MODE:
                raise RuntimeError(
                    f"ZIP member has non-canonical mode: {info.filename}"
                )
            if info.file_size > MAX_ENTRY_BYTES:
                raise RuntimeError(
                    f"ZIP member exceeds {MAX_ENTRY_BYTES} byte safety limit: "
                    f"{info.filename}"
                )
            total_uncompressed += info.file_size
        if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise RuntimeError("ZIP uncompressed payload exceeds safety limit")

        names = [info.filename for info in all_infos]
        if len(names) != len(set(names)):
            raise RuntimeError("ZIP contains duplicate member names")

        safe = [_safe_archive_name(name) for name in names]
        canonical_names = sorted(path.as_posix() for path in safe)
        if names != canonical_names:
            raise RuntimeError("ZIP member order is not canonical lexicographic order")

        roots = {path.parts[0] for path in safe}
        if len(roots) != 1:
            raise RuntimeError(f"ZIP contains multiple release roots: {sorted(roots)}")
        root_name = next(iter(roots))

        rows: dict[str, zipfile.ZipInfo] = {}
        folded_rows: set[str] = set()
        for path, info in zip(safe, all_infos):
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"ZIP contains symlink member: {info.filename}")
            rel = PurePosixPath(*path.parts[1:]).as_posix()
            if not rel:
                raise RuntimeError(f"ZIP contains invalid root-only file: {info.filename}")
            if rel in rows:
                raise RuntimeError(f"ZIP contains duplicate normalized path: {rel}")
            folded = rel.casefold()
            if folded in folded_rows:
                raise RuntimeError(f"ZIP contains case-colliding path: {rel}")
            folded_rows.add(folded)
            rows[rel] = info

        manifest_info = rows.pop("RELEASE_MANIFEST.json", None)
        if manifest_info is None:
            raise RuntimeError(
                f"{root_name}/RELEASE_MANIFEST.json missing from ZIP"
            )
        manifest_bytes = zf.read(manifest_info)
        manifest = _load_json_bytes(
            manifest_bytes,
            context="release manifest",
        )
        fmt = manifest.get("format")
        if fmt == "g502x-release-v2":
            release_name = manifest.get("release_name")
            if not isinstance(release_name, str) or root_name != release_name:
                raise RuntimeError("v2 ZIP root does not match release_name")
        elif fmt == "g502x-release-v1":
            if root_name != HISTORICAL_V1_RELEASE_NAME:
                raise RuntimeError("historical v1 ZIP root mismatch")

        _verify_payload(
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            actual_files=set(rows),
            read_bytes=lambda rel: zf.read(rows[rel]),
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a g502x release directory or deterministic ZIP."
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    if args.path.is_dir():
        verify_directory(args.path)
        kind = "directory"
    elif args.path.suffix.lower() == ".zip":
        verify_archive(args.path)
        kind = "archive"
    else:
        raise SystemExit("path must be a release directory or .zip archive")

    print(f"RELEASE VERIFY PASS ({kind}): {args.path}")


if __name__ == "__main__":
    main()
