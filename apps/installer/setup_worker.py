"""Local macOS operator installer, delivered with a wheel and public key.

No passwords/provider credentials/private SSH keys are shipped in the bundle.
Launching this installer authorizes its displayed local installation and grant.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import shlex
import stat
import subprocess
import sys
import time
import venv
from pathlib import Path


def checked_bundle(bundle: Path) -> tuple[Path, Path, dict]:
    bundle = bundle.resolve(strict=True)
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("version") != 1:
        raise ValueError("Unsupported pairing bundle version.")
    name = manifest["wheel"]
    if not isinstance(name, str) or Path(name).name != name or not name.startswith("aabridge-") or not name.endswith("-py3-none-any.whl"):
        raise ValueError("Invalid bundled wheel name.")
    wheel, public = bundle / name, bundle / "coordinator.pub"
    for path, expected in ((wheel, manifest["wheel_sha256"]), (public, manifest["public_key_sha256"])):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_194_304:
            raise ValueError("Missing or unsafe pairing artifact.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Pairing artifact checksum mismatch; nothing was installed.")
    if public.stat().st_size > 4096:
        raise ValueError("Public key exceeds the size limit.")
    return wheel, public, manifest


def append_authorized_key(home: Path, entry: str) -> bool:
    """Append our generated restricted entry, preserving existing key lines."""
    from aabridge_core.config import private_directory
    from aabridge_protocol.messages import require
    require(entry.startswith('restrict,command="') and " ssh-ed25519 " in entry and
            entry.endswith("\n") and entry.count("\n") == 1 and "\r" not in entry,
            "Only a generated restricted bridge key entry may be installed.")
    directory = private_directory(home / ".ssh", create=True)
    path = directory / "authorized_keys"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and
                not info.st_mode & 0o022, "Unsafe existing authorized_keys; nothing was replaced.")
        require(info.st_size <= 1_048_576, "Existing authorized_keys exceeds the installer size limit.")
        existing = os.read(descriptor, 1_048_577).decode()
        if entry.rstrip("\n") in existing.splitlines():
            return False
        public = "ssh-ed25519 " + entry.split(" ssh-ed25519 ", 1)[1].split()[0]
        require(public not in existing, "This public key already has a different SSH entry; review it locally.")
        os.fchmod(descriptor, 0o600)
        os.lseek(descriptor, 0, os.SEEK_END)
        addition = (("\n" if existing and not existing.endswith("\n") else "") + entry).encode()
        view = memoryview(addition)
        while view:
            written = os.write(descriptor, view)
            require(written > 0, "Could not append the bridge key.")
            view = view[written:]
        os.fsync(descriptor)
        return True
    finally:
        os.close(descriptor)


def bundled_codex(application: Path) -> Path | None:
    from aabridge_preflight.runtimes import bundled_codex as discover_bundle
    return discover_bundle(application)


def locate_codex(*, application_roots: tuple[Path, ...] | None = None, configured_path: str | None = None) -> Path:
    from aabridge_preflight.runtimes import locate_runtime
    return locate_runtime("codex", configured_path, application_roots=application_roots)


def runtime_settings(bundle: Path) -> dict:
    from aabridge_preflight.runtimes import local_path
    from aabridge_protocol.messages import decode, fields, require
    path = bundle / "runtime-paths.json"
    if not path.exists() and not path.is_symlink():
        return {}
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 8192,
            "Unsafe or oversized local runtime-paths.json.")
    value = fields(decode(path.read_bytes()), {"version"}, {"codex", "claude"})
    require(type(value["version"]) is int and value["version"] == 1, "Unsupported runtime path settings version.")
    result = {key: val for key, val in value.items() if key != "version"}
    for value in result.values():
        require(value is None or isinstance(value, str), "Runtime paths must be text or null for automatic discovery.")
        if value is not None:
            local_path(value)
    return result


def stop_idle_receiver(state: Path) -> None:
    """Upgrade only an idle owned receiver; never orphan an active worker."""
    import sqlite3
    from aabridge_cli.actions import request, send
    from aabridge_core.store import Store
    from aabridge_protocol.messages import BridgeError, require
    if not (state / "config.json").exists():
        return
    database = state / "bridge.sqlite3"
    if database.exists() or database.is_symlink():
        Store._validate_database(database)
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            for (document,) in connection.execute("SELECT document FROM tasks"):
                task = json.loads(document)
                require(task["status"] not in {"running", "queued", "cancelling"} and task.get("worker_pid") is None,
                        "A receiver task is still active or closing. Wait for it to finish closing, then select Install receiver again. No installation or permissions were changed.")
        finally:
            connection.close()
    try:
        send(state, request("shutdown", {}))
    except BridgeError as exc:
        require(exc.code == "daemon_unavailable", exc.message, exc.code)
    # Wait for the old supervisor's own lock, without signalling arbitrary PIDs.
    lock = state / "daemon.lock"
    if not lock.exists():
        return
    descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077,
                "Unsafe receiver lock; existing installation was preserved.")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, "The previous receiver is still closing. Wait, then select Install receiver again.")
                time.sleep(0.1)
    finally:
        os.close(descriptor)


# Each worker gets its own disposable demo, so Codex and Claude can coexist.
DEMOS = {"codex": "demo", "claude": "claude-demo"}


def configure(bundle: Path, install: Path, public: Path, manifest: dict, worker: str = "codex", claude_token: str | None = None):
    import asyncio
    from aabridge_cli.actions import CLAUDE_TOKEN, configure_runtimes, demo_init, enable_project, grant_peer, request, runtime_inventory, send, start_daemon, store_claude_token
    from aabridge_core.config import initialize, load_config, read_private, save_config
    from aabridge_preflight.paths import default_state_directory
    from aabridge_protocol.messages import identifier, require
    require(worker in DEMOS, "Choose Codex or Claude Code as the receiver worker.")
    name = "Claude Code" if worker == "claude" else "Codex"
    project_id = DEMOS[worker]
    state = default_state_directory()
    print("STEP: Checking selected agent locations", flush=True)
    if not (state / "config.json").exists():
        initialize(state)
    settings = runtime_settings(bundle)
    if settings:
        configure_runtimes(state, settings)
    runtimes = runtime_inventory(state)
    for agent, runtime in runtimes.items():
        print(f"{agent}: {runtime['status']}; {runtime.get('executable') or runtime.get('message')}", flush=True)
    require(runtimes[worker]["status"] == "found", runtimes[worker].get("message", f"{name} is unavailable."))
    executable = Path(runtimes[worker]["executable"])
    print(f"Receiver worker: {name}", flush=True)
    print(f"Using {name} executable: {executable}", flush=True)
    os.environ["PATH"] = str(executable.parent) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
    config = load_config(state)
    source = install / ("demo-source" if worker == "codex" else "claude-demo-source")
    if project_id not in config["projects"]:
        demo_init(state, source, project_id, worker)
    else:
        existing = config["projects"][project_id]
        require(existing["repository"] == str(source) and existing["adapter"] == worker,
                f"An existing {project_id} project belongs to another repository or worker; it was preserved.")
        if worker == "codex":
            require(existing["codex_executable"] == str(executable),
                    "Existing demo approval is pinned to another Codex executable. It was preserved; register a new project for an intentionally changed runtime.")
    config = load_config(state)
    if worker == "codex":
        login = shlex.join([str(executable), "-c", 'cli_auth_credentials_store="file"', "login"])
        require(Path(config["projects"][project_id]["codex_auth_file"]).is_file(),
                f"Codex needs a local file-based login for this worker. Run this command on the mini, choose Sign in with ChatGPT, then rerun Setup.command: {login}")
    else:
        if claude_token:
            store_claude_token(state, claude_token)
            print("Stored the Claude subscription token privately on this Mac (not printed).", flush=True)
        require((state / CLAUDE_TOKEN).is_file(),
                f"Claude Code needs a subscription token for this worker. In Terminal on this Mac run: {shlex.quote(str(executable))} setup-token — then paste the printed sk-ant-oat01-… token into Receiver setup and select Install receiver again.")
    # Checks authentication/isolation only: no model inference in setup.
    print(f"STEP: Checking whole-process isolation and the {name} runtime (no model inference)", flush=True)
    asyncio.run(enable_project(state, project_id))
    print(f"STEP: Installing restricted {project_id}-project SSH access", flush=True)
    peer_id = identifier(manifest["peer_id"])
    config = load_config(state)
    if peer_id not in config["peers"]:
        grant_peer(state, peer_id, [project_id], public)
    config = load_config(state)
    peer = config["peers"][peer_id]
    require(peer["enabled"], "Existing peer is revoked; no permissions were overwritten.")
    entry = read_private(state / f"authorized-key-{peer_id}.txt", 16384).decode()
    key_fields = public.read_text().strip().split()
    require(len(key_fields) >= 2 and f" {key_fields[0]} {key_fields[1]} " in entry,
            "Existing peer uses a different public key; nothing was replaced.")
    if project_id not in peer["projects"] and "*" not in peer["projects"]:
        # Same enrolled key: running this installer authorizes its demo grant.
        peer["projects"].append(project_id)
        save_config(state, config)
    require(all(name in config["projects"] for name in peer["projects"]),
            "Existing peer has a different policy; no permissions were overwritten.")
    append_authorized_key(Path.home().resolve(), entry)
    print("STEP: Starting the bridge supervisor", flush=True)
    start_daemon(state)
    ping = send(state, request("ping", {}))
    print("\nAABRIDGE MINI READY")
    print(json.dumps({"node_id": ping["node_id"], "project": project_id, "peer": peer_id,
                      "worker": worker, "model_inference_started": False}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--configure", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--diagnose", action="store_true", help="Check paths and isolation without installing, granting SSH access or running a model.")
    parser.add_argument("--worker", choices=tuple(DEMOS), default="codex", help="Managed worker agent for this receiver's demo project.")
    parser.add_argument("--claude-token-stdin", action="store_true", help="Read a claude setup-token token from standard input.")
    args = parser.parse_args()
    # Read before anything else; the token is never placed in argv, files or logs.
    claude_token = (sys.stdin.readline(1024).strip() or None) if args.claude_token_stdin else None
    wheel, public, manifest = checked_bundle(args.bundle)
    # Load the bundled pure-Python validation primitives before offline install.
    # Configuration itself is rerun under the installed environment below.
    if not args.configure:
        sys.path.insert(0, str(wheel))
    if args.diagnose:
        from aabridge_adapter_codex.isolation import isolation_report
        from aabridge_preflight.runtimes import runtime_report
        settings = runtime_settings(args.bundle)
        runtimes = {}
        for agent in ("codex", "claude"):
            report = runtime_report(agent, settings.get(agent))
            runtimes[agent] = report
            print(f"{agent}: {report['status']}; {report.get('executable') or report.get('message')}", flush=True)
        report = isolation_report()
        print(json.dumps(report, indent=2), flush=True)
        name = "Claude Code" if args.worker == "claude" else "Codex"
        if report["passed"] and runtimes[args.worker]["status"] == "found":
            print(f"DIAGNOSTICS PASSED: {name} found and all isolation canaries passed. Account and remote connection are not checked.", flush=True)
            return 0
        reason = (report["diagnostic"] or "Failed checks: " + ", ".join(report["failed_checks"])) if not report["passed"] else runtimes[args.worker]["message"]
        print("DIAGNOSTICS INCOMPLETE: " + reason, flush=True)
        return 1
    from aabridge_core.config import private_directory, read_private, write_private
    install = private_directory(Path.home().resolve() / "Library/Application Support/AABridgeInstall", create=True)
    if args.configure:
        configure(args.bundle.resolve(), install, public, manifest, args.worker, claude_token)
        return 0
    print("STEP: Installing the bundled bridge in its private Python environment", flush=True)
    print(f"Installing AABridge locally and granting only {DEMOS[args.worker]}-project bridge access.")
    print("Existing repositories, SSH keys and provider credentials are preserved.")
    environment = install / "venv"
    marker = install / "installer.json"
    if environment.exists():
        private_directory(environment)
        if json.loads(read_private(marker)).get("version") != 1:
            raise ValueError("Existing installation has an unsupported marker; preserved.")
        from aabridge_preflight.paths import default_state_directory
        print("STEP: Checking that the existing receiver is idle before upgrading", flush=True)
        stop_idle_receiver(default_state_directory())
    else:
        venv.EnvBuilder(with_pip=True).create(environment)
        environment.chmod(0o700)
        write_private(marker, b'{"version":1}', exclusive=True)
    python = environment / "bin/python"
    subprocess.run([str(python), "-m", "pip", "--disable-pip-version-check", "--no-cache-dir", "install",
                    "--no-index", "--no-deps", "--force-reinstall", str(wheel)], check=True)
    configured = subprocess.run([str(python), str(Path(__file__).resolve()), "--bundle", str(args.bundle.resolve()), "--configure",
                                 "--worker", args.worker, *(["--claude-token-stdin"] if claude_token else [])],
                                input=(claude_token + "\n").encode() if claude_token else None)
    return configured.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Bounded operator diagnostics; never dump credentials/provider logs.
        message = getattr(exc, "message", None) or (str(exc) if isinstance(exc, ValueError) else "A local setup step failed. Existing state was preserved. Check Python/Codex availability and rerun.")
        print("\nSETUP INCOMPLETE: " + message[:1000], file=sys.stderr)
        raise SystemExit(1)
