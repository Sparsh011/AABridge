"""Fixed local version/help probes. Never execute a command received from a peer."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass

from aabridge_preflight.models import Check

PROBE_TIMEOUT_SECONDS = 3
MAX_OUTPUT_BYTES = 16_384


@dataclass(frozen=True)
class Tool:
    name: str
    version_args: tuple[str, ...]
    version_pattern: str


TOOLS = (
    Tool("git", ("--version",), r"^git version ([0-9]+\.[0-9]+(?:\.[0-9]+)?)"),
    Tool("ssh", ("-V",), r"^OpenSSH_([0-9]+\.[0-9]+(?:p[0-9]+)?)"),
    Tool("codex", ("--version",), r"^codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z.-]+)?)"),
    Tool("claude", ("--version",), r"^([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)"),
)


@dataclass(frozen=True)
class ProbeOutput:
    status: str
    text: str = ""


def _run_probe(executable: str, args: tuple[str, ...]) -> ProbeOutput:
    # Version/help commands are trusted local installations, not sandboxed agents.
    # Spool output to a temporary file so a broken CLI cannot fill Python memory.
    # Do not inherit stdin, run shell syntax, print raw output, or keep probe logs.
    try:
        with tempfile.TemporaryDirectory(prefix="aabridge-probe-") as directory:
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(
                    [executable, *args], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, cwd=directory,
                    start_new_session=True, shell=False,
                )
                try:
                    process.wait(timeout=PROBE_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    _kill_group(process)
                    return ProbeOutput("timeout")
                except BaseException:
                    _kill_group(process)
                    raise
                if process.returncode != 0:
                    return ProbeOutput("failed")
                output.seek(0)
                data = output.read(MAX_OUTPUT_BYTES + 1)
                if len(data) > MAX_OUTPUT_BYTES:
                    return ProbeOutput("oversized")
                return ProbeOutput("ok", data.decode("utf-8", errors="replace"))
    except OSError:
        return ProbeOutput("failed")


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def check_tool(tool: Tool, *, configured_path: str | None = None) -> tuple[Check, str | None]:
    executable = shutil.which(tool.name)
    if tool.name in ("codex", "claude") and (configured_path is not None or executable is None):
        from aabridge_preflight.runtimes import locate_runtime
        try:
            executable = str(locate_runtime(tool.name, configured_path))
        except (ValueError, OSError):
            return Check(f"tool.{tool.name}", "warning", "Selected runtime is unavailable or automatic discovery found no unique executable.",
                         f"Check the drive/app location with aabridge runtime list; select {tool.name} locally using runtime set."), None
    if executable is None:
        return Check(f"tool.{tool.name}", "warning", "Not found on this process's PATH.", f"Install {tool.name} before its integration milestone."), None
    outcome = _run_probe(executable, tool.version_args)
    if outcome.status != "ok":
        return Check(f"tool.{tool.name}", "warning", f"Version probe {outcome.status}; raw output was omitted.", "Check the installed CLI locally; no login was attempted."), None
    match = re.search(tool.version_pattern, outcome.text, flags=re.MULTILINE)
    if match is None:
        return Check(f"tool.{tool.name}", "warning", "Version was not recognized; raw output was omitted."), None
    return Check(f"tool.{tool.name}", "ok", f"Version {match.group(1)} found; authentication was not checked."), executable


def check_agent_interface(name: str, executable: str) -> Check:
    if name == "codex":
        args, required = ("app-server", "--help"), ("--listen", "stdio://")
    elif name == "claude":
        args, required = ("--help",), ("--print", "--resume", "--output-format")
    else:
        raise ValueError("Only locally defined agent interfaces may be probed")
    outcome = _run_probe(executable, args)
    if outcome.status != "ok" or not all(token in outcome.text for token in required):
        return Check(f"interface.{name}", "warning", "Expected programmatic interface was not confirmed by CLI help.", "Verify this CLI version against its official documentation before enabling an adapter.")
    return Check(f"interface.{name}", "ok", "Expected programmatic options appear in CLI help; execution and exact-session resumption remain unverified.")
