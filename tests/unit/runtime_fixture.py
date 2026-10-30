"""Keep deterministic fixtures independent of agents installed on this Mac."""
from pathlib import Path

from aabridge_cli.actions import configure_runtimes


def select_fake_codex(state: Path) -> Path:
    executable = state.parent / (state.name + "-fixture-codex")
    executable.write_text("fixture only; must never execute")
    executable.chmod(0o755)
    configure_runtimes(state, {"codex": str(executable)})
    return executable
