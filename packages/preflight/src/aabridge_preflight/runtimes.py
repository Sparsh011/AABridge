"""Local, per-agent location discovery. Never execute or search an entire disk."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

AGENTS = ("codex", "claude")


def local_path(value: str | Path) -> Path:
    raw = str(value)
    if not raw or len(raw) > 2048 or any(ord(c) < 32 for c in raw):
        raise ValueError("Invalid local runtime path.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("Select an absolute app/executable path or a ~/ path.")
    return path


def bundled_codex(application: Path) -> Path | None:
    for relative in ("Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
                     "Contents/Resources/codex"):
        candidate = application / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve(strict=True)
    return None


def executable_at(agent: str, value: str | Path) -> Path:
    path = local_path(value)
    # Finder hides .app in many views; accept the selected app without its suffix.
    if agent == "codex" and not path.exists() and path.suffix != ".app" and path.with_name(path.name + ".app").is_dir():
        path = path.with_name(path.name + ".app")
    if agent == "codex" and path.is_dir():
        native = bundled_codex(path)
        if native:
            return native
        raise ValueError(f"No supported native Codex executable found inside the selected app: {path}")
    if not path.is_file() or not os.access(path, os.X_OK):
        drive = f" Reconnect the selected drive {path.parts[2]!r}." if len(path.parts) > 2 and path.parts[1] == "Volumes" else ""
        raise ValueError(f"Configured {agent} location is unavailable or not executable: {path}.{drive} Check this agent's selected path.")
    executable = path.resolve(strict=True)
    if agent == "codex":
        for application in reversed(executable.parents):
            if application.suffix == ".app":
                native = bundled_codex(application)
                if native:
                    return native
    return executable


def locate_runtime(agent: str, configured_path: str | Path | None = None, *,
                   application_roots: tuple[Path, ...] | None = None,
                   command_roots: tuple[Path, ...] | None = None) -> Path:
    if agent not in AGENTS:
        raise ValueError("Unsupported runtime name.")
    if configured_path is not None:
        # An explicit SSD selection is authoritative, even when unplugged.
        return executable_at(agent, configured_path)
    found = shutil.which(agent)
    if found:
        return executable_at(agent, found)
    candidates = set()
    if agent == "codex":
        roots = application_roots if application_roots is not None else (Path("/Applications"), Path.home() / "Applications")
        for root in roots:
            for name in ("ChatGPT.app", "Codex.app"):
                app = root / name
                if app.is_dir():
                    native = bundled_codex(app)
                    if native:
                        candidates.add(native)
    else:
        roots = command_roots if command_roots is not None else (Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
        for root in roots:
            path = root / agent
            if path.is_file() and os.access(path, os.X_OK):
                candidates.add(path.resolve(strict=True))
    if not candidates:
        raise ValueError(f"Could not locate {agent}. Select its existing app or executable using local runtime configuration; it may be on an external drive.")
    if len(candidates) != 1:
        raise ValueError(f"Multiple distinct {agent} installations found. Select the intended location explicitly.")
    return candidates.pop()


def runtime_report(agent: str, configured_path: str | Path | None = None, **search) -> dict:
    result = {"agent": agent, "configured_path": str(configured_path) if configured_path is not None else None,
              "authentication": "not_checked", "adapter_available": agent == "codex"}
    try:
        executable = locate_runtime(agent, configured_path, **search)
        result.update(status="found", executable=str(executable),
                      location="external_volume" if executable.parts[1:2] == ("Volumes",) else "local")
    except (ValueError, OSError) as exc:
        result.update(status="unavailable", executable=None, message=str(exc)[:3000])
    return result
