"""Local prerequisite inspection. No daemon, worker, SSH connection, or login."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

try:
    import sqlite3
except ImportError:
    sqlite3 = None  # type: ignore[assignment]

from aabridge_preflight.models import Check, Report
from aabridge_preflight.paths import check_state_directory, default_state_directory
from aabridge_preflight.probes import TOOLS, check_agent_interface, check_tool


def check_python() -> Check:
    current = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 11):
        return Check("runtime.python", "error", f"Python {current}; Python 3.11+ is required.", "Install Python 3.11 separately and recreate .venv.")
    return Check("runtime.python", "ok", f"Python {current}; minimum 3.11 satisfied.")


def check_sqlite() -> Check:
    if sqlite3 is None:
        return Check("runtime.sqlite", "error", "This Python installation has no SQLite support.", "Use a Python distribution built with SQLite support.")
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE probe (value INTEGER)")
            connection.execute("INSERT INTO probe VALUES (1)")
            connection.rollback()
            if connection.execute("SELECT count(*) FROM probe").fetchone() != (0,):
                raise sqlite3.DatabaseError("Rollback did not take effect")
        finally:
            connection.close()
    except sqlite3.Error:
        return Check("runtime.sqlite", "error", "SQLite in-memory transaction check failed.")
    return Check("runtime.sqlite", "ok", f"SQLite {sqlite3.sqlite_version}; in-memory transaction/rollback passed (disk durability not tested).")


def collect_report(*, state_dir: Path | None = None) -> Report:
    checks = [check_python(), check_sqlite()]
    checks.append(Check(
        "platform", "ok" if sys.platform == "darwin" else "error",
        "macOS foundation target." if sys.platform == "darwin" else "This milestone targets macOS; other platforms are not validated.",
    ))
    checks.append(Check(
        "ipc.unix_socket", "ok" if hasattr(socket, "AF_UNIX") else "error",
        "Unix socket API available; this diagnostic does not connect to the protected endpoint." if hasattr(socket, "AF_UNIX") else "Unix socket API unavailable.",
    ))
    state_path = state_dir if state_dir is not None else default_state_directory()
    state_check = check_state_directory(state_path)
    checks.append(state_check)
    selections = {}
    if state_check.status != "error" and (state_path / "config.json").is_file():
        from aabridge_core.config import load_config
        from aabridge_protocol.messages import BridgeError
        try:
            selections = load_config(state_path).get("runtimes", {})
        except (BridgeError, OSError, ValueError):
            checks.append(Check("runtime.locations", "warning", "Local runtime selections could not be inspected safely."))
    for tool in TOOLS:
        configured = selections.get(tool.name, {}).get("path")
        check, executable = check_tool(tool, configured_path=configured) if configured is not None else check_tool(tool)
        checks.append(check)
        if executable and tool.name in ("codex", "claude"):
            checks.append(check_agent_interface(tool.name, executable))
    checks.extend((
        Check("security.worker_isolation", "warning", "This diagnostic does not verify worker isolation or enable project execution; use the separate project-enable preflight."),
        Check("auth.agents", "warning", "Provider credentials were not read and unattended authentication was not tested."),
        Check("transport.remote", "warning", "This diagnostic did not perform peer enrollment, host-key verification, or a physical two-device test."),
    ))
    return Report(tuple(checks))
