from __future__ import annotations

from contextlib import contextmanager
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path


PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PRIVATE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@contextmanager
def exclusive_operation_lock(path: str | Path):
    """Acquire a non-blocking cross-process lock for one device transaction.

    The lock file is persistent private state; ownership is held by the OS file
    lock and is automatically released when the process/file descriptor exits.
    """
    target = Path(path)
    private_mkdir(target.parent)

    fd = os.open(
        target,
        os.O_RDWR | os.O_CREAT,
        PRIVATE_FILE_MODE,
    )
    handle = os.fdopen(fd, "r+b", buffering=0)
    locked = False
    try:
        _chmod_private(target, PRIVATE_FILE_MODE)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())

        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    handle.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except (BlockingIOError, OSError) as exc:
            raise RuntimeError(
                "another g502x hardware operation is already active for this user"
            ) from exc

        locked = True
        yield target
    finally:
        if locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(
                        handle.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_UN,
                    )
            finally:
                locked = False
        handle.close()


def normalize_private_component(
    value: str,
    *,
    context: str = "private state label",
) -> str:
    """Validate a user-controlled name that becomes one path component."""
    if not isinstance(value, str):
        raise ValueError(f"{context}: expected a string")
    normalized = value.strip()
    if not PRIVATE_COMPONENT_RE.fullmatch(normalized):
        raise ValueError(
            f"{context}: use 1-64 characters from A-Z, a-z, 0-9, dot, dash or underscore; "
            "the first character must be alphanumeric"
        )
    return normalized


def _chmod_private(path: Path, mode: int) -> None:
    """Best-effort POSIX permission hardening.

    Windows ACLs are not represented by POSIX mode bits, so do not claim that
    chmod there provides the same security boundary.
    """
    if os.name == "posix":
        os.chmod(path, mode)


def _fsync_dir(path: Path) -> None:
    """Best-effort directory metadata durability on POSIX."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_mkdir(
    path: str | Path,
    *,
    parents: bool = True,
    exist_ok: bool = True,
) -> Path:
    root = Path(path)
    root.mkdir(
        mode=PRIVATE_DIR_MODE,
        parents=parents,
        exist_ok=exist_ok,
    )
    _chmod_private(root, PRIVATE_DIR_MODE)
    return root


def private_write_bytes(path: str | Path, data: bytes) -> Path:
    """Atomically replace a local private-state file.

    The temporary file is created in the destination directory with 0600 on
    POSIX and then atomically replaced into place. Existing destination modes
    are therefore not inherited from a permissive umask/old file.
    """
    target = Path(path)
    private_mkdir(target.parent)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp = Path(tmp_name)
    try:
        _chmod_private(tmp, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _chmod_private(target, PRIVATE_FILE_MODE)
        _fsync_dir(target.parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def private_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    return private_write_bytes(Path(path), text.encode(encoding))


def atomic_local_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Atomically replace a user-selected local text file without following links.

    Unlike private_write_text(), this does not chmod the destination directory.
    It is appropriate for shareable reports or explicit export paths while still
    preventing partial files and symlink redirection.
    """
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError(
            f"output path symbolic links are not accepted: {target}"
        )
    parent = target.parent
    if parent.is_symlink() or not parent.exists() or not parent.is_dir():
        raise RuntimeError(
            f"output parent must be an existing real directory: {parent}"
        )
    if target.exists() and not target.is_file():
        raise RuntimeError(
            f"output path must be a regular file: {target}"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp = Path(tmp_name)
    try:
        _chmod_private(tmp, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode(encoding))
            handle.flush()
            os.fsync(handle.fileno())
        if target.is_symlink():
            raise RuntimeError(
                f"output path became a symbolic link during write: {target}"
            )
        os.replace(tmp, target)
        _chmod_private(target, PRIVATE_FILE_MODE)
        _fsync_dir(parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def harden_private_dir(path: str | Path) -> None:
    """Harden an existing local private-state directory if it exists."""
    root = Path(path)
    if root.exists() and root.is_dir():
        _chmod_private(root, PRIVATE_DIR_MODE)



def require_private_regular_file(
    path: str | Path,
    *,
    context: str,
) -> Path:
    """Reject symlink/special-file indirection for security-sensitive state."""
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError(f"{context}: symbolic links are not accepted: {target}")
    if not target.exists():
        raise RuntimeError(f"{context}: file missing: {target}")
    if not target.is_file():
        raise RuntimeError(f"{context}: expected a regular file: {target}")
    return target


def require_private_directory(
    path: str | Path,
    *,
    context: str,
) -> Path:
    """Reject symlink/special-directory indirection for private state roots."""
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError(f"{context}: symbolic links are not accepted: {target}")
    if not target.exists():
        raise RuntimeError(f"{context}: directory missing: {target}")
    if not target.is_dir():
        raise RuntimeError(f"{context}: expected a directory: {target}")
    return target



def private_stage_dir(
    parent: str | Path,
    *,
    prefix: str = ".partial-",
) -> Path:
    """Create a private staging directory on the destination filesystem."""
    root = private_mkdir(parent)
    stage = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))
    _chmod_private(stage, PRIVATE_DIR_MODE)
    return stage


def private_commit_dir(staging: str | Path, target: str | Path) -> Path:
    """Atomically publish a complete private-state directory.

    The target must not already exist. The staging directory must share the
    target parent so the rename stays on one filesystem.
    """
    stage = Path(staging)
    out = Path(target)
    require_private_directory(stage, context="private staging directory")
    if stage.parent.resolve() != out.parent.resolve():
        raise ValueError("private directory commit must stay within one parent")
    if out.is_symlink() or out.exists():
        raise FileExistsError(out)

    _fsync_dir(stage)
    os.replace(stage, out)
    _chmod_private(out, PRIVATE_DIR_MODE)
    _fsync_dir(out.parent)
    return out

def private_discard_dir(path: str | Path) -> None:
    """Remove unpublished private staging without following symlinks."""
    root = Path(path)
    if root.is_symlink():
        root.unlink()
        return
    if not root.exists():
        return
    require_private_directory(
        root,
        context="private staging directory cleanup",
    )
    shutil.rmtree(root)


def private_replace_dir(
    staging: str | Path,
    target: str | Path,
    *,
    previous_prefix: str = ".previous-",
) -> Path:
    """Publish staging while retaining the previous directory until commit.

    Normal exceptions roll the old directory back. An abrupt process/system
    interruption can leave the old directory under a hidden previous path;
    callers may recover that copy if the target is absent on the next start.
    """
    stage = Path(staging)
    out = Path(target)
    parent = out.parent

    require_private_directory(stage, context="private replacement staging")
    if stage.parent.resolve() != parent.resolve():
        raise ValueError("private directory replace must stay within one parent")
    if out.is_symlink():
        raise RuntimeError(
            f"private replacement target cannot be a symbolic link: {out}"
        )
    if not out.exists():
        return private_commit_dir(stage, out)
    require_private_directory(out, context="private replacement target")

    previous = parent / (
        f"{previous_prefix}{out.name}-{uuid.uuid4().hex}"
    )
    if previous.is_symlink() or previous.exists():
        raise RuntimeError(f"private rollback path already exists: {previous}")

    _fsync_dir(stage)
    os.replace(out, previous)
    _fsync_dir(parent)
    try:
        os.replace(stage, out)
        _chmod_private(out, PRIVATE_DIR_MODE)
        _fsync_dir(parent)
    except Exception:
        if out.is_symlink():
            out.unlink()
        elif out.exists():
            shutil.rmtree(out)
        os.replace(previous, out)
        _chmod_private(out, PRIVATE_DIR_MODE)
        _fsync_dir(parent)
        raise

    shutil.rmtree(previous)
    _fsync_dir(parent)
    return out
