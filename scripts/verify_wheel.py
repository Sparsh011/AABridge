"""Install a built wheel offline in a disposable environment outside the repo."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import venv
import zipfile
import tomllib
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    release = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheels = sorted((root / "dist").glob(f"aabridge-{release}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one AABridge wheel in dist/; review build artifacts.")
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for expected in ("aabridge_cli/main.py", "aabridge_preflight/doctor.py", "aabridge_daemon/server.py",
                         "aabridge_preflight/runtimes.py",
                         "aabridge_mcp/server.py", "aabridge_adapter_codex/worker.py", "aabridge_adapter_claude/worker.py", "aabridge_transport_ssh/client.py",
                         "aabridge_protocol/schemas/v1/request.schema.json"):
            if expected not in names:
                raise SystemExit(f"Wheel is missing {expected}")
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        if "Requires-Python: >=3.11" not in archive.read(metadata_name).decode():
            raise SystemExit("Wheel does not declare the Python 3.11 minimum.")
    with tempfile.TemporaryDirectory(prefix="aabridge-wheel-") as directory:
        temporary = Path(directory).resolve()
        environment = temporary / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / "bin" / "python"
        cli = environment / "bin" / "aabridge"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--no-index", "--no-deps", str(wheel)],
            cwd=temporary, check=True,
        )
        subprocess.run([str(cli), "--version"], cwd=temporary, check=True)
        state = temporary / "uninitialized-state"
        result = subprocess.run(
            [str(cli), "doctor", "--json", "--state-dir", str(state)],
            cwd=temporary, text=True, capture_output=True, check=True,
        )
        report = json.loads(result.stdout)
        if report["schema_version"] != 1 or report["worker_execution_enabled"] is not False:
            raise SystemExit("Unexpected doctor schema or worker execution enabled.")
        if state.exists():
            raise SystemExit("Doctor created persistent state.")
        if report["verification"]["real_two_device_agents"] != "not_verified":
            raise SystemExit("Doctor incorrectly claims real remote verification.")
        subprocess.run([str(cli), "init", "--state-dir", str(state)], cwd=temporary,
                       capture_output=True, text=True, check=True)
        locations = subprocess.run([str(cli), "runtime", "set", "--state-dir", str(state), "--agent", "codex",
                                    "--path", str(temporary / "unmounted drive/ChatGPT.app")],
                                   cwd=temporary, capture_output=True, text=True, check=True)
        location_report = json.loads(locations.stdout)["codex"]
        if location_report["status"] != "unavailable" or location_report["authentication"] != "not_checked":
            raise SystemExit("Installed runtime location guard did not report an offline selection safely.")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        mcp = subprocess.run([str(cli), "mcp", "--state-dir", str(state)], cwd=temporary,
                            input="".join(json.dumps(message) + "\n" for message in messages),
                            capture_output=True, text=True, check=True, timeout=5)
        responses = [json.loads(line) for line in mcp.stdout.splitlines()]
        advertised = responses[-1]["result"]["tools"]
        if len(advertised) != 17 or not {"bridge_conversation", "bridge_team_start", "bridge_team_status", "bridge_team_activity", "bridge_activity"} <= {tool["name"] for tool in advertised}:
            raise SystemExit("Installed MCP facade did not load its packaged schemas/tools.")
        # Verify this Python actually imports the installed wheel, not source paths.
        imported = subprocess.run(
            [str(python), "-I", "-c", "import aabridge_cli; print(aabridge_cli.__file__)"],
            cwd=temporary, text=True, capture_output=True, check=True,
        )
        if not Path(imported.stdout.strip()).is_relative_to(environment):
            raise SystemExit("Smoke check imported the source checkout instead of the wheel.")
    print("Wheel metadata, offline installation, CLI/doctor, initialization and MCP/schema checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
