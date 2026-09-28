#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from release_metadata import parse_requirements_lock, sha256_file, write_spdx


ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "g502x-onboard"
METADATA_FILES = {"RELEASE_MANIFEST.json", "SBOM.spdx.json"}
REQUIRED_V02_RELEASE_FILES = {
    "g502x.py",
    "g502x_tui.py",
    "requirements.txt",
    "requirements-tui.txt",
    "g502x_onboard/tui/__init__.py",
    "g502x_onboard/tui/app.py",
    "g502x_onboard/tui/bootstrap.py",
    "g502x_onboard/tui/effects.py",
    "g502x_onboard/tui/events.py",
    "g502x_onboard/tui/model.py",
    "g502x_onboard/tui/runner.py",
    "g502x_onboard/tui/update.py",
    "g502x_onboard/tui/view.py",
    "docs/RELEASE_NOTES_V0.2.0.md",
    "tools/extracted_release_smoke.py",
    "tools/verify_release.py",
}


def git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_clean_tracked_tree() -> None:
    dirty = git(["status", "--porcelain=v1", "--untracked-files=no"])
    if dirty:
        raise RuntimeError(
            "public release requires committed tracked inputs:\n" + dirty
        )


def tracked_files() -> list[Path]:
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    paths = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = Path(item.decode("utf-8"))
        if rel.as_posix() in METADATA_FILES:
            raise RuntimeError(
                f"generated release metadata must not be tracked as source: {rel}"
            )
        if rel.parts and rel.parts[0] == "dist":
            continue
        source = ROOT / rel
        if source.is_symlink():
            raise RuntimeError(f"release refuses tracked symlink: {rel}")
        if not source.is_file():
            raise RuntimeError(f"tracked release input is not a file: {rel}")
        paths.append(rel)
    return sorted(paths, key=lambda p: p.as_posix())


def project_version() -> str:
    text = (ROOT / "g502x_onboard" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("unable to read g502x version")
    return match.group(1)


def _require_v02_surface(paths: list[Path], version: str) -> None:
    if version != "0.2.0":
        return
    actual = {path.as_posix() for path in paths}
    missing = sorted(REQUIRED_V02_RELEASE_FILES - actual)
    if missing:
        raise RuntimeError(f"v0.2.0 release inputs missing required files: {missing}")


def assert_canonical_generated_newlines(root: Path) -> None:
    for rel in (
        "README.md",
        "requirements.txt",
        "requirements-tui.txt",
        "docs/RELEASE_NOTES_V0.2.0.md",
        "SBOM.spdx.json",
        "RELEASE_MANIFEST.json",
    ):
        path = root / rel
        if not path.exists():
            continue
        data = path.read_bytes()
        if b"\r\n" in data or b"\r" in data:
            raise RuntimeError(
                f"release generated text is not canonical LF-only: {rel}"
            )


def deterministic_zip(
    root: Path,
    archive: Path,
    *,
    root_name: str | None = None,
) -> None:
    if archive.exists():
        archive.unlink()
    files = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    archive_root = root.name if root_name is None else root_name
    if not archive_root or Path(archive_root).name != archive_root:
        raise RuntimeError(f"invalid release archive root: {archive_root!r}")

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in files:
            arcname = (Path(archive_root) / path.relative_to(root)).as_posix()
            info = zipfile.ZipInfo(
                filename=arcname,
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes())


def _requirements_manifest(path: str, text: str, source: Path) -> dict:
    return {
        "path": path,
        "sha256": sha256_file(source),
        "hash_checking": "--require-hashes" in text,
        "packages": parse_requirements_lock(text),
    }


def build(out: Path, expected_tag: str | None = None) -> tuple[Path, Path]:
    require_clean_tracked_tree()
    version = project_version()
    if expected_tag and expected_tag != f"v{version}":
        raise RuntimeError(
            f"tag/version mismatch: {expected_tag} != v{version}"
        )

    source_commit = git(["rev-parse", "HEAD"])
    source_timestamp = git(["show", "-s", "--format=%cI", source_commit])
    core_path = ROOT / "requirements.txt"
    tui_path = ROOT / "requirements-tui.txt"
    requirements_text = core_path.read_text(encoding="utf-8")
    optional_requirements_text = tui_path.read_text(encoding="utf-8")
    core_packages = parse_requirements_lock(requirements_text)
    optional_packages = parse_requirements_lock(optional_requirements_text)
    overlap = set(core_packages) & set(optional_packages)
    if overlap:
        raise RuntimeError(
            "core and optional dependency locks overlap: "
            + ", ".join(sorted(overlap))
        )

    source_files = tracked_files()
    _require_v02_surface(source_files, version)

    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.mkdir()

    for rel in source_files:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        blob = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "show",
                f"HEAD:{rel.as_posix()}",
            ],
            check=True,
            capture_output=True,
        ).stdout
        dst.write_bytes(blob)

    write_spdx(
        out / "SBOM.spdx.json",
        version=version,
        source_commit=source_commit,
        source_timestamp=source_timestamp,
        requirements_text=requirements_text,
        optional_requirements_text=optional_requirements_text,
        hidapi_files={
            "x64": out / "libs" / "x64" / "hidapi.dll",
            "x86": out / "libs" / "x86" / "hidapi.dll",
        },
    )

    files = {}
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "RELEASE_MANIFEST.json":
            rel = path.relative_to(out).as_posix()
            files[rel] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }

    manifest = {
        "format": "g502x-release-v2",
        "version": version,
        "release_name": f"{PROJECT_NAME}-{version}",
        "source_commit": source_commit,
        "source_inputs_clean": True,
        "entrypoints": {
            "cli": "g502x.py",
            "tui": "g502x_tui.py",
        },
        "archive_reproducible": True,
        "archive_layout": {
            "order": "lexicographic",
            "timestamp": "1980-01-01T00:00:00",
            "compression": "stored",
            "file_mode": "0644",
        },
        "private_state_included": False,
        "python_requirements": {
            "core": _requirements_manifest("requirements.txt", requirements_text, core_path),
            "optional_tui": _requirements_manifest(
                "requirements-tui.txt",
                optional_requirements_text,
                tui_path,
            ),
        },
        "sbom": {
            "path": "SBOM.spdx.json",
            "format": "SPDX-2.3",
            "sha256": sha256_file(out / "SBOM.spdx.json"),
        },
        "files": files,
    }
    (out / "RELEASE_MANIFEST.json").write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )

    assert_canonical_generated_newlines(out)

    archive = out.parent / f"{out.name}.zip"
    deterministic_zip(out, archive, root_name=f"{PROJECT_NAME}-{version}")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_bytes(
        f"{sha256_file(archive)}  {archive.name}\n".encode("ascii")
    )
    return out, archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a deterministic G502 X Onboard release."
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--expected-tag")
    args = parser.parse_args()

    default_out = ROOT / "dist" / f"{PROJECT_NAME}-{project_version()}"
    out = (args.out or default_out).resolve()
    out, archive = build(out, args.expected_tag)
    print(f"Release directory: {out}")
    print(f"Release archive:   {archive}")
    print(f"Archive SHA-256:   {sha256_file(archive).upper()}")


if __name__ == "__main__":
    main()
