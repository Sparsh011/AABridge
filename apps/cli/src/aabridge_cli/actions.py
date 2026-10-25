from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from aabridge_core.config import initialize, load_config, private_directory, read_private, save_config, token_digest, write_private
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError, Request, decode, identifier, require, validate_request
from aabridge_transport_local.client import call as local_call
from aabridge_transport_ssh.client import call as remote_call
from aabridge_workspace.git import git, relative_file, safe_source, scoped_source, validate_verifier
from aabridge_preflight.runtimes import AGENTS, local_path, runtime_report

CLAUDE_TOKEN = "claude-oauth-token"


def configure_runtimes(state: Path, paths: dict) -> dict:
    """Local operator selections only; existing project approvals stay pinned."""
    require(isinstance(paths, dict) and set(paths) <= set(AGENTS), "Unknown runtime selection.")
    for value in paths.values():
        if value is not None:
            require(isinstance(value, str), "Runtime path must be text or null for automatic discovery.")
            local_path(value)
    config = load_config(state)
    selections = config.setdefault("runtimes", {})
    for agent, value in paths.items():
        selections[agent] = {"path": value}
    save_config(state, config)
    return runtime_inventory(state)


def runtime_inventory(state: Path) -> dict:
    config = load_config(state)
    return {agent: runtime_report(agent, config.get("runtimes", {}).get(agent, {}).get("path")) for agent in AGENTS}


def request(op: str, body: dict, key: str | None = None) -> Request:
    return validate_request({"version": 1, "op": op, "key": key or "req-" + secrets.token_hex(12), "body": body})


def send(state: Path, value: Request, remote: str | None = None) -> dict:
    if remote:
        config = load_config(state)
        require(remote in config["remotes"], "Remote is not enrolled locally.", "permission_denied")
        return remote_call(config["remotes"][remote], value)
    return local_call(state, value)


def register_project(state: Path, project_id: str, repository: Path, verification: dict, agent: str = "codex") -> dict:
    identifier(project_id)
    require(agent in AGENTS, "Unknown worker runtime.")
    config = load_config(state)
    require(project_id not in config["projects"], "Project is already registered; existing policy was not overwritten.")
    repository = repository.expanduser().resolve(strict=True)
    require(repository.is_dir() and not repository.is_relative_to(state), "Register a source repository outside bridge state.")
    verification = validate_verifier(verification)
    base = git(repository, "rev-parse", "HEAD")
    scope = verification["files"] if verification["profile"] == "file-proposal-v1" else [verification["file"]]
    for path in scope:
        scoped_source(repository, path, allow_new=verification["profile"] == "file-proposal-v1")
    project = {"repository": str(repository), "adapter": agent, "worker": agent, "scope": scope,
               "base_commit": base, "verification": verification, "execution_enabled": False,
               "limits": {"runtime_seconds": 180, "max_turns": 4, "max_revisions": 1}}
    project.update(runtime_binding(state, agent))
    config["projects"][project_id] = project
    save_config(state, config)
    return {"project_id": project_id, "worker": agent, "base_commit": base, "execution_enabled": False}


def runtime_binding(state: Path, agent: str) -> dict:
    """Pin the locally discovered executable and its credential location."""
    discovered = runtime_inventory(state)[agent]
    require(discovered["status"] == "found", discovered.get("message", f"{agent} is unavailable."), "adapter_unavailable")
    executable = Path(discovered["executable"])
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if agent == "claude":
        return {"claude_executable": str(executable), "claude_sha256": digest, "claude_token_file": str(state / CLAUDE_TOKEN)}
    auth = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    return {"codex_executable": str(executable), "codex_sha256": digest, "codex_auth_file": str(auth)}


def store_claude_token(state: Path, token: str | None) -> dict:
    """Local operator credential storage; never accepted through bridge RPC/MCP."""
    from aabridge_adapter_claude.worker import validate_token
    path = state / CLAUDE_TOKEN
    if token is None:
        if path.exists() or path.is_symlink():
            read_private(path, 1024)  # Only remove a verified private regular file.
            path.unlink()
        return {"claude_token": "removed"}
    write_private(path, validate_token(token).encode())
    return {"claude_token": "stored", "path": str(path), "note": "The token was not printed. Run project enable for each Claude project to revalidate."}


def configure_file_project(state: Path, project_id: str, repository: Path, scope: list[str], peers: list[str], agent: str = "codex") -> dict:
    """Local operator configuration only; never exposed through bridge RPC/MCP."""
    identifier(project_id)
    profile = validate_verifier({"profile": "file-proposal-v1", "files": scope})
    config = load_config(state)
    require(isinstance(peers, list) and len(peers) <= 32 and all(isinstance(peer, str) for peer in peers), "Choose enrolled peers.")
    require(len(set(peers)) == len(peers), "Duplicate peer selection.")
    for peer in peers:
        require(peer != "local" and peer in config["peers"] and config["peers"][peer]["enabled"], "Choose an enabled enrolled peer.")
    store = Store(state)
    try:
        for (document,) in store.db.execute("SELECT document FROM tasks WHERE project=?", (project_id,)):
            task = json.loads(document)
            require(task["status"] in {"completed", "failed", "cancelled"} and task.get("worker_pid") is None,
                    "Finish or cancel this project's active tasks before editing its scope.", "project_busy")
    finally:
        store.close()
    repository = repository.expanduser().resolve(strict=True)
    require(repository.is_dir() and not repository.is_relative_to(state), "Choose a Git repository outside bridge state.")
    base = git(repository, "rev-parse", "HEAD")
    entries = git(repository, "ls-tree", "-r", "--full-tree", base).splitlines()
    require(len(entries) <= 5000 and all(entry.split(" ", 1)[0] in {"100644", "100755"} for entry in entries),
            "Symlinks, submodules, and repositories with over 5000 files are unsupported.", "unsafe_workspace")
    tracked = set(git(repository, "ls-tree", "-r", "--name-only", base).splitlines())
    for path in scope:
        _, source = scoped_source(repository, path, allow_new=True)
        require(source is None or path in tracked, "An existing scoped file must be committed before registration; new absent files are allowed.")
        require(not any(name.startswith(path + "/") for name in tracked), "A scoped path is a directory in the approved baseline.")
    existing = config["projects"].get(project_id)
    if existing is None:
        register_project(state, project_id, repository, profile, agent)
        config = load_config(state)
    else:
        require(existing["adapter"] == agent, "This project uses a different worker. Create a new project ID to use another agent.")
        require(existing["verification"]["profile"] == "file-proposal-v1", "Keep the demo intact; create a new file-proposal project ID.")
    project = config["projects"][project_id]
    project.update(repository=str(repository), base_commit=base, scope=scope, verification=profile, execution_enabled=False)
    for peer_id, peer in config["peers"].items():
        if peer_id == "local" or "*" in peer["projects"]:
            continue
        peer["projects"] = [name for name in peer["projects"] if name != project_id]
        if peer_id in peers:
            peer["projects"].append(project_id)
    save_config(state, config)
    return {"project_id": project_id, "base_commit": base, "scope": scope, "execution_enabled": False}


async def enable_project(state: Path, project_id: str) -> dict:
    from aabridge_adapter_codex.isolation import probe_isolation
    from aabridge_daemon.server import WORKERS
    config = load_config(state)
    require(project_id in config["projects"], "Project is not registered.")
    project = config["projects"][project_id]
    require(project["adapter"] in WORKERS, "No managed worker adapter exists for this project's runtime.", "adapter_unavailable")
    # Enabling is the local revalidation step: pin the currently selected binary.
    project.update(runtime_binding(state, project["adapter"]))
    project["execution_enabled"] = False
    save_config(state, config)
    from aabridge_adapter_codex.native import NativeWorker
    coding = project.get("permission_profile") == "coding-v1"
    evidence = {"mode": "native-cli"} if coding else probe_isolation()
    worker = (NativeWorker if coding else WORKERS[project["adapter"]])(state, {"id": "preflight-" + secrets.token_hex(8)}, project, lambda *args: None, lambda pid: None)
    try:
        await asyncio.wait_for(worker.start(), 20)
    finally:
        await worker.close()
    # No model turn in the enabling preflight. Local operator, never peer RPC.
    project["execution_enabled"] = True
    project["isolation_evidence"] = evidence
    save_config(state, config)
    return {"project_id": project_id, "execution_enabled": True, "isolation": evidence}


def demo_init(state: Path, directory: Path, project_id: str, agent: str = "codex") -> dict:
    require(agent in AGENTS, "Unknown worker runtime.")
    runtime = runtime_inventory(state)[agent]
    require(runtime["status"] == "found", runtime.get("message", f"{agent} is unavailable."), "adapter_unavailable")
    directory = directory.expanduser().absolute()
    require(not directory.exists() and not directory.is_symlink(), "Demo directory must not already exist.")
    directory.mkdir(mode=0o700, parents=False)
    directory = directory.resolve()
    (directory / "math_ops.py").write_text("def add(a, b):\n    return a - b\n")
    git(directory, "init", "--initial-branch=main", "--template=")
    git(directory, "add", "--", "math_ops.py")
    git(directory, "commit", "-m", "Disposable AABridge arithmetic demo")
    profile = {"profile": "integer-function-v1", "file": "math_ops.py", "function": "add",
               "cases": [{"args": [2, 3], "expected": 5}, {"args": [-2, 3], "expected": 1}, {"args": [0, 0], "expected": 0}]}
    return {**register_project(state, project_id, directory, profile, agent), "demo_repository": str(directory)}


def start_daemon(state: Path) -> dict:
    try:
        return {"already_running": True, **local_call(state, request("ping", {}))}
    except BridgeError as exc:
        require(exc.code == "daemon_unavailable", exc.message, exc.code)
    # The child has its own session and no SSH/terminal stdio dependency.
    process = subprocess.Popen([sys.executable, "-m", "aabridge_cli", "serve", "--state-dir", str(state)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True, cwd=state)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BridgeError("daemon_failed", "Daemon failed to start. Run aabridge serve in the foreground to see the bounded error.")
        try:
            return {"started": True, **local_call(state, request("ping", {}))}
        except BridgeError:
            time.sleep(0.1)
    raise BridgeError("daemon_failed", "Daemon did not become ready within five seconds.")


def grant_peer(state: Path, peer_id: str, projects: list[str], public_key: Path) -> dict:
    identifier(peer_id)
    require(peer_id != "local", "The local operator identity cannot be enrolled as a peer.")
    config = load_config(state)
    require(peer_id not in config["peers"], "Peer identity already exists; do not silently replace keys.")
    require(all(name in config["projects"] for name in projects), "Assign only existing explicit projects.")
    require(public_key.stat().st_size <= 4096, "Public key file is too large.")
    parts = public_key.read_text().strip().split()
    require(len(parts) >= 2 and parts[0] == "ssh-ed25519", "Use a dedicated Ed25519 SSH public key.")
    try:
        base64.b64decode(parts[1], validate=True)
    except ValueError:
        raise BridgeError("invalid_request", "Malformed SSH public key.") from None
    token = secrets.token_urlsafe(32)
    write_private(state / f"peer-{peer_id}.token", token.encode(), exclusive=True)
    config["peers"][peer_id] = {"digest": token_digest(token), "enabled": True, "projects": projects}
    executable = str(Path(sys.executable).parent / "aabridge")
    command = shlex.join([executable, "rpc", "--state-dir", str(state), "--peer", peer_id])
    require("\n" not in command and "\r" not in command, "SSH entry point paths may not contain newlines.")
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    entry = f'restrict,command="{escaped}" {parts[0]} {parts[1]} aabridge-{peer_id}\n'
    path = state / f"authorized-key-{peer_id}.txt"
    write_private(path, entry.encode(), exclusive=True)
    save_config(state, config)
    return {"peer_id": peer_id, "projects": projects, "authorized_keys_entry": str(path),
            "next": "Append this entry to the intended SSH account's authorized_keys after local review. No private key or bridge token was printed."}


def enroll_remote(state: Path, name: str, host: str, user: str, port: int, identity_file: Path, known_hosts: Path, fingerprint: str) -> dict:
    from aabridge_transport_ssh.client import ssh_arguments
    identifier(name)
    config = load_config(state)
    require(name not in config["remotes"], "Remote already enrolled; pinned keys were not replaced.")
    require(1 <= port <= 65535 and re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint) is not None, "Invalid port or trusted fingerprint.")
    require(known_hosts.stat().st_size <= 16384, "Host-key input is too large.")
    target = host if port == 22 else f"[{host}]:{port}"
    result = subprocess.run(["/usr/bin/ssh-keygen", "-F", target, "-f", str(known_hosts)], capture_output=True, timeout=5)
    require(result.returncode == 0, "No matching host key found in the supplied host-key file.")
    approved = []
    for line in result.stdout.decode().splitlines():
        if line.startswith("#"):
            continue
        with tempfile.NamedTemporaryFile() as stream:
            stream.write((line + "\n").encode()); stream.flush()
            check = subprocess.run(["/usr/bin/ssh-keygen", "-lf", stream.name], capture_output=True, timeout=5)
            if check.returncode == 0 and check.stdout.decode().split()[1] == fingerprint:
                approved.append(line)
    require(bool(approved), "Host key does not match the fingerprint obtained through the trusted setup channel.", "host_identity_mismatch")
    pinned = state / f"known-hosts-{name}"
    write_private(pinned, ("\n".join(approved) + "\n").encode(), exclusive=True)
    remote = {"host": host, "user": user, "port": port, "identity_file": str(identity_file.expanduser().absolute()), "known_hosts": str(pinned)}
    ssh_arguments(remote)
    config["remotes"][name] = remote
    save_config(state, config)
    return {"remote": name, "fingerprint": fingerprint, "network_tested": False}


def restricted_rpc(state: Path, peer: str) -> None:
    identifier(peer)
    require(peer != "local", "SSH cannot assume the local operator identity.", "permission_denied")
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    if original != "aabridge-rpc-v1":
        # Git's transport is also bounded by fixed IDs, never a received path.
        parts = shlex.split(original)
        if len(parts) == 2 and parts[0] == "git-upload-pack":
            components = parts[1].split("/")
            if len(components) == 4 and components[:2] == ["", "aabridge"]:
                project_id, task_id = map(identifier, components[2:])
                task = local_call(state, request("status", {"task_id": task_id}), peer=peer)
                require(task["project_id"] == project_id and task["status"] == "completed", "No completed authorized result is available.", "permission_denied")
                path = state / "workspaces" / task_id
                require(path.resolve() == path and git(path, "rev-parse", "HEAD") == task["result"]["result_commit"], "Result workspace identity changed.")
                metadata = path / ".git"
                require(metadata.is_dir() and not metadata.is_symlink(), "Unexpected task Git metadata.")
                os.execve("/usr/bin/git", ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "upload-pack", "--strict", "--timeout=30", str(metadata)],
                          {"PATH": "/usr/bin:/bin", "HOME": str(state), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        raise BridgeError("permission_denied", "Only fixed bridge RPC and authorized Git result reads are permitted over this SSH key.")
    payload = sys.stdin.buffer.readline(131073)
    try:
        response = {"result": local_call(state, validate_request(decode(payload)), peer=peer)}
    except BridgeError as exc:
        # A valid transport exchange can carry an operation-level denial. Keep
        # its structured code so clients distinguish replay from connectivity.
        response = {"error": {"code": exc.code, "message": exc.message}}
    print(json.dumps(response))


def fetch_result(state: Path, remote_name: str, task_id: str) -> dict:
    from aabridge_protocol.messages import SHA
    from aabridge_transport_ssh.client import ssh_arguments
    task = send(state, request("status", {"task_id": task_id}), remote_name)
    require(task["status"] == "completed", "Task has no completed result.")
    result = task["result"]
    require(SHA.fullmatch(result["base_commit"]) and SHA.fullmatch(result["result_commit"]), "Remote result contains invalid commit IDs.")
    project_id = identifier(task["project_id"])
    config = load_config(state)
    remote = config["remotes"][remote_name]
    review_root = private_directory(state / "reviews", create=True)
    review = review_root / identifier(task_id)
    if not review.exists():
        review.mkdir(mode=0o700)
        git(review, "init", "--bare", "--template=", "--object-format=" + ("sha256" if len(result["result_commit"]) == 64 else "sha1"))
    require(review.resolve() == review, "Review repository cannot be a symlink.")
    uri = f"ssh://{remote['user']}@{remote['host']}:{remote['port']}/aabridge/{project_id}/{task_id}"
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(state), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_VARIANT": "ssh", "GIT_SSH_COMMAND": shlex.join(ssh_arguments(remote))}
    outcome = subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(review), "fetch", "--no-tags", uri, result["result_commit"]],
                             env=environment, capture_output=True, timeout=40)
    require(outcome.returncode == 0, "Authenticated Git result transfer failed; no active checkout was changed.", "git_transfer_failed")
    require(git(review, "rev-parse", "FETCH_HEAD") == result["result_commit"], "Fetched commit identity mismatch.")
    diff = git(review, "diff", "--no-ext-diff", "--no-textconv", result["base_commit"], result["result_commit"])
    require(len(diff) <= 65536, "Result diff exceeds the review limit.")
    return {"review_repository": str(review), "base_commit": result["base_commit"], "result_commit": result["result_commit"], "diff": diff}


def configure_coding_project(state: Path, project_id: str, repository: Path, peers: list[str], agent: str = 'codex') -> dict:
    """Local admin enables normal coding tools for a whole repository."""
    identifier(project_id)
    require(agent in AGENTS, 'Choose Codex or Claude.')
    config = load_config(state)
    require(isinstance(peers, list) and all(p in config['peers'] and p != 'local' and config['peers'][p]['enabled'] for p in peers), 'Choose enrolled Macs.')
    store = Store(state)
    try:
        require(not store.db.execute("SELECT 1 FROM tasks WHERE project=? AND status NOT IN ('completed','failed','cancelled')", (project_id,)).fetchone(),
                'Finish or stop active project tasks before changing settings.', 'project_busy')
    finally:
        store.close()
    repository = repository.expanduser().resolve(strict=True)
    require(repository.is_dir() and not repository.is_relative_to(state), 'Choose a project folder outside bridge state.')
    require(git(repository, 'rev-parse', '--show-toplevel') == str(repository), 'Choose the root of the Git project.')
    project = {'repository': str(repository), 'adapter': agent, 'worker': agent, 'scope': ['*'],
               'base_commit': git(repository, 'rev-parse', 'HEAD'), 'verification': {'profile': 'repository-v1'},
               'permission_profile': 'coding-v1', 'execution_enabled': False,
               'limits': {'runtime_seconds': 3600, 'max_turns': 100, 'max_revisions': 100}, **runtime_binding(state, agent)}
    config['projects'][project_id] = project
    for name, peer in config['peers'].items():
        if name == 'local' or '*' in peer['projects']:
            continue
        peer['projects'] = [p for p in peer['projects'] if p != project_id] + ([project_id] if name in peers else [])
    save_config(state, config)
    return {'project_id': project_id, 'base_commit': project['base_commit']}
