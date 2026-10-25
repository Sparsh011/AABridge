"""Opt-in real localhost OpenSSH/Git test. No model calls or system SSH edits.

Creates private disposable state and keys, retaining them for inspection. The
authorized-key entry lives under the OS user temp directory because OpenSSH
StrictModes rejects /private/tmp as an authorized_keys ancestor on macOS.
"""
from __future__ import annotations

import getpass
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from aabridge_cli.actions import demo_init, enroll_remote, fetch_result, grant_peer, request, send, start_daemon
from aabridge_core.config import initialize, load_config, save_config, write_private
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError
from aabridge_transport_ssh.client import call, ssh_arguments
from aabridge_workspace.git import Workspace


def main():
    root = Path(tempfile.mkdtemp(prefix="aab-ssh-", dir="/private/tmp")).resolve()
    approved = Path(tempfile.mkdtemp(prefix="aabridge-ssh-keys-")).resolve()
    worker, coordinator = root / "worker", root / "coordinator state"
    initialize(worker)
    initialize(coordinator)
    for name in ("host", "client", "wrong-host"):
        subprocess.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)], check=True)
    demo_init(worker, root / "source", "demo")
    grant = grant_peer(worker, "coordinator", ["demo"], root / "client.pub")
    entry = approved / "authorized_keys"
    write_private(entry, Path(grant["authorized_keys_entry"]).read_bytes(), exclusive=True)
    config = load_config(worker)
    project = config["projects"]["demo"]
    body = {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Transport fixture, no model inference", "scope": project["scope"],
            "acceptance": ["Pass local profile"], "context": [], "limits": project["limits"], "permission_profile": "proposal-v1"}
    # Deliberately constructed result fixture: this tests transport, not an agent.
    store = Store(worker)
    task = store.handle(request("submit", body, "fixture"), "coordinator", config)
    workspace = Workspace(worker, task, project)
    workspace.prepare()
    result = workspace.result({"summary": "Transport fixture", "issues": [],
        "edits": [{"path": "math_ops.py", "content": "def add(a, b):\n    return a + b\n"}]})
    store.update(task["id"], "result", status="completed", result=result)
    store.close()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    ssh_config = root / "sshd_config"
    write_private(ssh_config, f"""Port {port}
ListenAddress 127.0.0.1
HostKey {root}/host
PidFile {root}/sshd.pid
AuthorizedKeysFile {entry}
StrictModes yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
PermitRootLogin no
AllowUsers {getpass.getuser()}
DisableForwarding yes
PermitTTY no
LogLevel ERROR
""".encode())
    daemon = None
    try:
        with (root / "sshd.log").open("wb") as log:
            daemon = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(ssh_config)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        time.sleep(.25)
        if daemon.poll() is not None:
            raise RuntimeError("Disposable unprivileged sshd did not start; inspect its private log.")
        host = (root / "host.pub").read_text().split()
        known = root / "host-known"
        write_private(known, f"[127.0.0.1]:{port} {host[0]} {host[1]}\n".encode())
        fingerprint = subprocess.check_output(["/usr/bin/ssh-keygen", "-lf", str(root / "host.pub")], text=True).split()[1]
        enroll_remote(coordinator, "loopback", "127.0.0.1", getpass.getuser(), port, root / "client", known, fingerprint)
        start_daemon(worker)
        assert send(coordinator, request("ping", {}), "loopback")["version"] == 1
        submitted = send(coordinator, request("submit", body, "remote-submission"), "loopback")
        assert send(coordinator, request("submit", body, "remote-submission"), "loopback")["id"] == submitted["id"]
        listed = send(coordinator, request("tasks", {"cursor": 0}), "loopback")
        assert {entry["id"] for entry in listed["tasks"]} == {task["id"], submitted["id"]}
        history = send(coordinator, request("conversation", {"task_id": task["id"], "cursor": 0}), "loopback")
        assert any(entry["kind"] == "task" and entry["text"] == body["objective"] for entry in history["messages"])
        assert any(entry["kind"] == "verified_result" for entry in history["messages"])
        try:
            send(coordinator, request("submit", {**body, "objective": "Changed content"}, "remote-submission"), "loopback")
            raise AssertionError("Changed-content replay was accepted")
        except BridgeError as exc:
            assert exc.code == "replay_conflict"
        assert send(coordinator, request("status", {"task_id": task["id"]}), "loopback")["status"] == "completed"
        transfer = fetch_result(coordinator, "loopback", task["id"])
        assert transfer["result_commit"] == result["result_commit"] and "+    return a + b" in transfer["diff"]
        remote = load_config(coordinator)["remotes"]["loopback"]
        denied = subprocess.run([*ssh_arguments(remote), "--", remote["host"], "uname"], capture_output=True, timeout=10)
        assert denied.returncode != 0
        wrong = (root / "wrong-host.pub").read_text().split()
        wrong_known = root / "wrong-known"
        write_private(wrong_known, f"[127.0.0.1]:{port} {wrong[0]} {wrong[1]}\n".encode())
        try:
            call({**remote, "known_hosts": str(wrong_known)}, request("ping", {}))
            raise AssertionError("Wrong host identity accepted")
        except BridgeError as exc:
            assert exc.code == "host_identity_mismatch"
        config["peers"]["coordinator"]["enabled"] = False
        save_config(worker, config)
        try:
            send(coordinator, request("status", {"task_id": task["id"]}), "loopback")
            raise AssertionError("Revoked peer accepted")
        except BridgeError:
            pass
        print(json.dumps({"passed": True, "category": "real_loopback_ssh_with_non_model_fixture",
            "checks": ["pinned authenticated RPC", "submission and replay", "Git commit and diff transfer",
                       "generic command denial", "actual host-key mismatch", "peer revocation"],
            "physical_two_device": "not_verified", "state": str(root), "authorized_entry": str(entry)}, indent=2))
    finally:
        try:
            send(worker, request("shutdown", {}))
        except BridgeError:
            pass
        if daemon and daemon.poll() is None:
            os.killpg(daemon.pid, signal.SIGTERM)
            try:
                daemon.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(daemon.pid, signal.SIGKILL)
                daemon.wait(timeout=3)


if __name__ == "__main__":
    main()
