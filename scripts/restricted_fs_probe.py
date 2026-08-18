"""Probe medium-integrity directories without mutating existing objects."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path


def _can_open_directory_for_write(path: Path) -> bool:
    kernel32 = ctypes.windll.kernel32
    create_file = kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x00000002 | 0x00010000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        return False
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return True


def _can_create_new_file(path: Path) -> bool:
    kernel32 = ctypes.windll.kernel32
    create_file = kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x40000000,
        0x00000001,
        None,
        1,
        0x00000080,
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        return False
    kernel32.CloseHandle(ctypes.c_void_p(handle))
    return True


def main() -> int:
    if sys.platform != "win32" or len(sys.argv) < 2:
        return 2
    for raw_target in sys.argv[1:]:
        root = Path(raw_target).resolve(strict=True)
        if _can_open_directory_for_write(root):
            return 1
        if _can_create_new_file(root / "low-integrity-probe-created.tmp"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
