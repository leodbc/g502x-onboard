# SPDX-License-Identifier: GPL-3.0-only
# Derived from lexr1/omm.py:
# https://github.com/lexr1/omm.py
# Upstream commit: 8e6c3f00a20acbde1d3001244c9ca24db613804f
# See NOTICE.md and docs/PROVENANCE.md.

# Modified in G502 X Onboard for release/runtime safety helpers.
import hashlib
import os
import struct
import sys
from pathlib import Path
import json

def pretty_json(j):
    return json.dumps(j, indent=2, ensure_ascii=False)

def save_file(filename, data):
    print(f'saving {filename}')
    try:
        if isinstance(data, str):
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(data)
        if isinstance(data, bytearray) or isinstance(data, bytes):
            with open(filename, 'wb') as f:
                f.write(data)
        return True
    except Exception as e:
        print(e)
        return False
        
def save_json_to_file(filename, j):
    save_file(filename, pretty_json(j))

def load_from_file(filename, filetype):
    assert os.path.exists(filename), f'error opening {filename}'    
    if filetype == 'json':
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    if filetype == 'bin':
        with open(filename, 'rb') as f:
            return f.read()
    if filetype == 'string':
        with open(filename, 'r', encoding='utf-8') as f:
            return f.read()
        
def load_bin_from_file(filename):
    if (os.path.exists(filename)):
        with open(filename, 'rb') as f:
            return f.read()
    else:
        print('error while loading', filename)
        return b''

def pretty_list(data):
    if isinstance(data, bytearray):
        data = list(data)
    return " ".join("0x{:02x}".format(x) for x in data)

def pretty_list2(data):
    if isinstance(data, bytearray):
        data = list(data)
    return " ".join("{:02x}".format(x) for x in data)

#https://stackoverflow.com/a/30357446/5007748
def crc16_ccitt(data):
    crc = 0xFFFF
    data = bytearray(data)
    msb = crc >> 8
    lsb = crc & 0xFF
    for c in data:
        x = c ^ msb
        x ^= (x >> 4)
        msb = (lsb ^ (x >> 3) ^ (x << 4)) & 0xFF
        lsb = (x ^ (x << 5)) & 0xFF
    return (msb << 8) + lsb

def str2int(v):
    if v.lower() in ['yes', 'true', 'y', '1', 'on']:
        return 1
    elif v.lower() in ['no', 'false', 'n', '0', 'off']:
        return 0
    else:
        return -1

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
WINDOWS_HIDAPI_SHA256 = {
    "x64": "d4c05ba2138cb5259a7f796464b5eaa63b9f8f67bb5cb003f96989244ee01583",
    "x86": "d4c8e5f799fc5f57038d492f13a687471d5d353447ead7e6cae70e957fb55ae1",
}


def escape_bytes(data):
    """Canonical legacy JSON representation: every byte as \\xNN."""
    return "".join(f"\\x{byte:02x}" for byte in bytes(data))


def unescape_bytes(value):
    """Decode only the canonical/legacy repeated \\xNN byte form.

    This deliberately rejects general Python-style escapes. Existing omm.py
    JSON emitted by escape_bytes/all-escapes remains compatible, while malformed
    or ambiguous text fails closed.
    """
    if not isinstance(value, str):
        raise TypeError("escaped bytes must be a string")
    if len(value) % 4 != 0:
        raise ValueError("escaped bytes must be a sequence of \\xNN tokens")

    out = bytearray()
    for offset in range(0, len(value), 4):
        token = value[offset:offset + 4]
        if (
            len(token) != 4
            or token[:2] != "\\x"
            or token[2] not in _HEX_DIGITS
            or token[3] not in _HEX_DIGITS
        ):
            raise ValueError(
                f"invalid escaped byte token at offset {offset}: {token!r}"
            )
        out.append(int(token[2:], 16))
    return bytes(out)


def _windows_hidapi_path(
    module_file,
    *,
    pointer_bits=None,
):
    pointer_bits = (
        struct.calcsize("P") * 8
        if pointer_bits is None
        else int(pointer_bits)
    )
    if pointer_bits not in (32, 64):
        raise ImportError(
            f"unsupported Python pointer width for bundled hidapi: {pointer_bits}"
        )
    arch = "x64" if pointer_bits == 64 else "x86"
    return Path(module_file).resolve().parent / arch / "hidapi.dll"


def prepare_windows_hidapi_directory(
    module_file,
    *,
    platform=None,
    pointer_bits=None,
    add_dll_directory=None,
):
    """Prepare the vendored hidapi DLL search directory on Windows.

    The returned handle is intentionally owned by the caller for as long as
    ctypes/pyhidapi may need the search path. CPython removes the directory when
    that handle is closed or collected.
    """
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return None

    dll_path = _windows_hidapi_path(
        module_file,
        pointer_bits=pointer_bits,
    )
    dll_dir = dll_path.parent
    arch = dll_dir.name
    if not dll_path.is_file():
        raise ImportError(
            f"bundled Windows hidapi.dll missing for {arch}: {dll_path}"
        )

    add = os.add_dll_directory if add_dll_directory is None else add_dll_directory
    return add(str(dll_dir))



def verify_windows_hidapi_loaded(
    module_file,
    hid_module,
    *,
    platform=None,
    pointer_bits=None,
    get_module_filename=None,
    expected_version=(0, 15, 0),
    expected_hashes=None,
):
    """Fail closed if Windows loaded a non-bundled or unexpected hidapi DLL."""
    platform = sys.platform if platform is None else platform
    if platform != "win32":
        return None

    expected = _windows_hidapi_path(
        module_file,
        pointer_bits=pointer_bits,
    )
    if not expected.is_file():
        raise ImportError(
            f"bundled Windows hidapi.dll missing: {expected}"
        )

    version = tuple(getattr(hid_module, "version", ()))
    if version != tuple(expected_version):
        raise ImportError(
            "loaded hidapi version does not match bundled release invariant: "
            f"{version!r} != {tuple(expected_version)!r}"
        )

    native = getattr(hid_module, "hidapi", None)
    native_handle = getattr(native, "_handle", None)
    if not isinstance(native_handle, int) or native_handle <= 0:
        raise ImportError(
            "unable to identify loaded hidapi native module handle"
        )

    if get_module_filename is None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_path = kernel32.GetModuleFileNameW
        get_path.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        get_path.restype = ctypes.c_uint32

        def get_module_filename(handle):
            buf = ctypes.create_unicode_buffer(32768)
            size = get_path(handle, buf, len(buf))
            if size == 0 or size >= len(buf):
                raise OSError(
                    ctypes.get_last_error(),
                    "GetModuleFileNameW failed for hidapi handle",
                )
            return buf.value

    actual = Path(get_module_filename(native_handle)).resolve()
    expected_resolved = expected.resolve()
    try:
        same_file = os.path.samefile(actual, expected_resolved)
    except (OSError, AttributeError):
        same_file = (
            os.path.normcase(str(actual))
            == os.path.normcase(str(expected_resolved))
        )
    if not same_file:
        raise ImportError(
            "pyhidapi loaded an unexpected native library; "
            f"expected bundled {expected}, got {actual}"
        )

    arch = expected.parent.name
    hashes = WINDOWS_HIDAPI_SHA256 if expected_hashes is None else expected_hashes
    expected_digest = hashes.get(arch)
    if expected_digest is None:
        raise ImportError(f"no pinned hidapi SHA-256 for architecture: {arch}")
    digest = hashlib.sha256(expected.read_bytes()).hexdigest()
    if digest != expected_digest:
        raise ImportError(
            "bundled hidapi.dll SHA-256 does not match release invariant"
        )
    return actual
