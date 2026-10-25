"""Inspect permissions without creating, opening, or repairing bridge state.

These are diagnostic snapshots, not race-free authorization primitives. Future
state readers must use descriptor-based, no-follow opens and validate again.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from aabridge_preflight.models import Check

# Only future bridge-owned entries are inspected. No arbitrary home directory scan.
STATE_FILES = ("config.toml", "bridge.sqlite3", "bridge.sqlite3-wal", "bridge.sqlite3-shm", "known_hosts", "peers.json", "bridge.sock")


def default_state_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AABridge"
    base = os.environ.get("XDG_STATE_HOME")
    if base and Path(base).is_absolute():
        return Path(base) / "aabridge"
    return Path.home() / ".local" / "state" / "aabridge"


def check_state_directory(path: Path) -> Check:
    remedy = "Choose an owned, private state directory (0700) with private files (0600); review symlinks locally."
    try:
        path = path.expanduser()
        if not path.is_absolute() or ".." in path.parts:
            return Check("state.permissions", "error", "State directory must be absolute without parent traversal.", remedy)
        # Do not resolve: that would hide the symlinks this diagnostic must reject.
        for parent in (*reversed(path.parents), path):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                return Check("state.permissions", "warning", "State directory is not initialized; no files were created.")
            if stat.S_ISLNK(info.st_mode):
                return Check("state.permissions", "error", "State path contains a symlink; inspection stopped.", remedy)
            if not stat.S_ISDIR(info.st_mode):
                return Check("state.permissions", "error", "State path contains a non-directory.", remedy)
        info = path.lstat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            return Check("state.permissions", "error", "State directory ownership or permissions are unsafe.", remedy)
        for name in STATE_FILES:
            try:
                info = (path / name).lstat()
            except FileNotFoundError:
                continue
            expected_kind = stat.S_ISSOCK if name.endswith(".sock") else stat.S_ISREG
            if not expected_kind(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                return Check("state.permissions", "error", "A bridge state entry has unsafe type, ownership, or permissions.", remedy)
        return Check("state.permissions", "ok", "Existing state directory and recognized entries have private ownership/permissions; contents were not read.")
    except (OSError, RuntimeError):
        return Check("state.permissions", "error", "State path could not be safely inspected.", remedy)
