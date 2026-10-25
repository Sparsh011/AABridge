"""Local, per-agent location discovery. Never execute or search an entire disk."""
from __future__ import annotations

import os
import plistlib
import shutil
from pathlib import Path

AGENTS = ("codex", "claude")
CLAUDE_CODE_BUNDLE = "com.anthropic.claude-code"
CLAUDE_DESKTOP_BUNDLE = "com.anthropic.claudefordesktop"


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


def bundle_identifier(application: Path) -> str | None:
    try:
        with (application / "Contents/Info.plist").open("rb") as stream:
            value = plistlib.load(stream).get("CFBundleIdentifier")
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) else None


def bundled_claude(application: Path) -> Path | None:
    # Identify the bundle, not the file name: on case-insensitive volumes the
    # Claude desktop app's own "Claude" binary also matches Contents/MacOS/claude.
    if bundle_identifier(application) != CLAUDE_CODE_BUNDLE:
        return None
    candidate = application / "Contents/MacOS/claude"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve(strict=True)
    return None


def desktop_claude_roots() -> tuple[Path, ...]:
    # The Claude desktop app keeps its own Claude Code CLI per version.
    return (Path.home() / "Library/Application Support/Claude/claude-code",)


def _version(name: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in name.split("."))
    except ValueError:
        return ()


def desktop_managed_claude(roots: tuple[Path, ...] | None = None) -> list[Path]:
    """Claude Code CLIs managed by the desktop app, newest version first."""
    found = []
    for root in roots if roots is not None else desktop_claude_roots():
        for application in root.glob("*/*/claude.app") if root.is_dir() else ():
            native = bundled_claude(application)
            if native:
                found.append((_version(application.parent.parent.name), native))
    return [native for _, native in sorted(found, key=lambda item: item[0], reverse=True)]


def claude_from_application(application: Path, desktop_roots: tuple[Path, ...] | None = None) -> Path:
    native = bundled_claude(application)
    if native:
        return native
    if bundle_identifier(application) == CLAUDE_DESKTOP_BUNDLE:
        # Selecting Claude.app means the Claude Code CLI that this app manages.
        managed = desktop_managed_claude(desktop_roots)
        if managed:
            return managed[0]
        raise ValueError("The Claude desktop app is selected, but its bundled Claude Code CLI was not found. Open a Claude Code session in the desktop app once, or choose the standalone claude executable (for example ~/.local/bin/claude).")
    raise ValueError(f"The selected app is not Claude Code: {application}. Choose the Claude desktop app or the standalone claude executable.")


def executable_at(agent: str, value: str | Path) -> Path:
    path = local_path(value)
    if agent == "claude" and path.suffix == ".app" and path.is_dir():
        return claude_from_application(path)
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
    if agent == "claude":
        # An executable chosen inside an app bundle must belong to Claude Code.
        for application in reversed(executable.parents):
            if application.suffix == ".app":
                return claude_from_application(application)
    return executable


def locate_runtime(agent: str, configured_path: str | Path | None = None, *,
                   application_roots: tuple[Path, ...] | None = None,
                   command_roots: tuple[Path, ...] | None = None,
                   desktop_roots: tuple[Path, ...] | None = None) -> Path:
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
            # Fixed two-level layout only: <version>/<build>/claude.app. No disk search.
            candidates.update(desktop_managed_claude(desktop_roots))
    if not candidates:
        raise ValueError(f"Could not locate {agent}. Select its existing app or executable using local runtime configuration; it may be on an external drive.")
    if len(candidates) != 1:
        raise ValueError(f"Multiple distinct {agent} installations found. Select the intended location explicitly.")
    return candidates.pop()


def runtime_report(agent: str, configured_path: str | Path | None = None, **search) -> dict:
    result = {"agent": agent, "configured_path": str(configured_path) if configured_path is not None else None,
              "authentication": "not_checked", "adapter_available": agent in AGENTS}
    try:
        executable = locate_runtime(agent, configured_path, **search)
        result.update(status="found", executable=str(executable),
                      location="external_volume" if executable.parts[1:2] == ("Volumes",) else "local")
    except (ValueError, OSError) as exc:
        result.update(status="unavailable", executable=None, message=str(exc)[:3000])
    return result
