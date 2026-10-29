"""Local operator commands and bounded agent task operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from importlib.metadata import version
from pathlib import Path
from typing import Sequence

from aabridge_preflight.doctor import collect_report
from aabridge_preflight.paths import default_state_directory
from aabridge_protocol.messages import BridgeError, decode, identifier, require, validate_request


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aabridge", description="AABridge: managed coding-agent communication."
    )
    parser.add_argument("--version", action="version", version=version("aabridge"))
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Inspect local prerequisites without starting workers or logging in."
    )
    doctor.add_argument("--json", action="store_true", help="Print a versioned JSON report.")
    doctor.add_argument(
        "--state-dir", type=Path, help="Inspect this local state directory without creating it."
    )
    for name in ("init", "start", "serve", "stop", "ping", "isolation", "mcp"):
        command = commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=default_state_directory())
    rpc = commands.add_parser("rpc", help="Restricted SSH forced-command entry point.")
    rpc.add_argument("--state-dir", type=Path, required=True)
    rpc.add_argument("--peer", required=True)
    card = commands.add_parser('connection-card', help='Create an app-managed direct TLS connection card; no Remote Login required.')
    card.add_argument('--state-dir', type=Path, default=default_state_directory())
    card.add_argument('--host', default='', help='Reachable LAN/VPN IPv4 address or hostname; defaults to this Mac’s .local name.')
    card.add_argument('--port', type=int, help='Local app port for first-time setup (default 47631).')
    connect = commands.add_parser('connect', help='Pair a public connection card read from stdin.')
    connect.add_argument('--state-dir', type=Path, default=default_state_directory())
    demo = commands.add_parser("demo-init", help="Create and register a new disposable arithmetic repository.")
    demo.add_argument("--state-dir", type=Path, default=default_state_directory())
    demo.add_argument("--directory", type=Path, required=True)
    demo.add_argument("--project", default="demo")
    demo.add_argument("--agent", choices=("codex", "claude"), default="codex", help="Managed worker runtime for this project.")
    project = commands.add_parser("project")
    project.add_argument("action", choices=("list", "register", "enable", "disable", "reconcile"))
    project.add_argument("--state-dir", type=Path, default=default_state_directory())
    project.add_argument("--id")
    project.add_argument("--repository", type=Path)
    project.add_argument("--verification", type=Path)
    project.add_argument("--remote")
    project.add_argument("--agent", choices=("codex", "claude"), default="codex", help="Managed worker runtime for register.")
    runtime = commands.add_parser("runtime", help="Local per-agent app/executable locations; no login or model call.")
    runtime.add_argument("action", choices=("list", "set"))
    runtime.add_argument("--state-dir", type=Path, default=default_state_directory())
    runtime.add_argument("--agent", choices=("codex", "claude"))
    selection = runtime.add_mutually_exclusive_group()
    selection.add_argument("--path")
    selection.add_argument("--auto", action="store_true")
    token = commands.add_parser("claude-token", help="Store or remove this Mac's Claude subscription token from claude setup-token.")
    token.add_argument("action", choices=("set", "remove"))
    token.add_argument("--state-dir", type=Path, default=default_state_directory())
    peer = commands.add_parser("peer")
    peer.add_argument("action", choices=("grant", "revoke"))
    peer.add_argument("--state-dir", type=Path, default=default_state_directory())
    peer.add_argument("--id", required=True)
    peer.add_argument("--project", action="append", default=[])
    peer.add_argument("--public-key", type=Path)
    remote = commands.add_parser("remote-add")
    remote.add_argument("--state-dir", type=Path, default=default_state_directory())
    for name in ("name", "host", "user", "fingerprint"):
        remote.add_argument("--" + name, required=True)
    remote.add_argument("--port", type=int, default=22)
    remote.add_argument("--identity-file", type=Path, required=True)
    remote.add_argument("--known-hosts", type=Path, required=True)
    task = commands.add_parser("task")
    task.add_argument("action", choices=("list", "conversation", "submit", "status", "events", "wait", "answer", "revise", "cancel", "fetch"))
    task.add_argument("--state-dir", type=Path, default=default_state_directory())
    task.add_argument("--remote")
    task.add_argument('--shared', action='store_true', help='Read local coding history in receiver-approved projects; does not grant task controls.')
    task.add_argument("--id")
    task.add_argument("--key")
    task.add_argument("--file", type=Path)
    task.add_argument("--project")
    task.add_argument("--objective")
    task.add_argument("--acceptance", action="append", default=[])
    task.add_argument("--revision", type=int)
    task.add_argument("--question-id")
    task.add_argument("--text")
    task.add_argument("--cursor", type=int, default=0)
    task.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args(argv)
    if args.command != "doctor":
        try:
            result = dispatch(args)
            if result is not None:
                print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except BridgeError as exc:
            print(json.dumps({"error": {"code": exc.code, "message": exc.message}}))
            return 1
        except (OSError, ValueError, KeyError, TypeError):
            print(json.dumps({"error": {"code": "local_configuration_error", "message": "Invalid local arguments, permissions, paths, or configuration. No secrets or raw provider output were returned."}}))
            return 1
    report = collect_report(state_dir=args.state_dir)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print("AABridge foundation preflight (report v1)")
        for check in report.checks:
            print(f"[{check.status.upper()}] {check.id}: {check.summary}")
            if check.remedy:
                print(f"  Next: {check.remedy}")
        print("\nDoctor does not enable execution or inspect project execution gates.")
        print("Use project list for gate status and project enable for isolation/authentication checks.")
        print("This diagnostic does not perform live local-agent or two-device tests.")
    return 1 if report.has_errors else 0


def dispatch(args):
    from aabridge_cli import actions
    from aabridge_core.config import initialize, load_config, private_directory, save_config
    from aabridge_core.store import Store
    from aabridge_adapter_codex.isolation import probe_isolation
    state = args.state_dir.expanduser().absolute()
    if args.command == "init":
        return initialize(state)
    if args.command == "isolation":
        return probe_isolation()
    private_directory(state)
    if args.command in {'connection-card', 'connect'}:
        from aabridge_core.pairing import card, connect
        if args.command == 'connection-card':
            return card(state, host=args.host, port=args.port)
        import sys
        return connect(state, decode(sys.stdin.buffer.read(131073)))
    if args.command == "runtime":
        if args.action == "list":
            return actions.runtime_inventory(state)
        require(args.agent is not None and (args.path is not None or args.auto), "Select --agent and either --path or --auto.")
        return actions.configure_runtimes(state, {args.agent: args.path})
    if args.command == "start":
        return actions.start_daemon(state)
    if args.command == "serve":
        from aabridge_daemon.server import serve
        asyncio.run(serve(state))
        return None
    if args.command in {"ping", "stop"}:
        return actions.send(state, actions.request("shutdown" if args.command == "stop" else "ping", {}))
    if args.command == "rpc":
        actions.restricted_rpc(state, identifier(args.peer))
        return None
    if args.command == "mcp":
        from aabridge_mcp.server import run
        run(state)
        return None
    if args.command == "demo-init":
        return actions.demo_init(state, args.directory, args.project, args.agent)
    if args.command == "claude-token":
        if args.action == "remove":
            return actions.store_claude_token(state, None)
        import getpass
        import sys
        # Read from a hidden prompt or stdin, never from argv or the environment.
        value = getpass.getpass("Paste the token printed by claude setup-token: ") if sys.stdin.isatty() else sys.stdin.readline(1024)
        return actions.store_claude_token(state, value)
    if args.command == "remote-add":
        return actions.enroll_remote(state, args.name, args.host, args.user, args.port, args.identity_file, args.known_hosts, args.fingerprint)
    if args.command == "peer":
        if args.action == "grant":
            require(args.public_key is not None, "--public-key is required for local peer approval.")
            return actions.grant_peer(state, args.id, args.project, args.public_key)
        config = load_config(state)
        require(args.id != "local" and args.id in config["peers"], "Select an enrolled remote peer.")
        config["peers"][args.id]["enabled"] = False
        save_config(state, config)
        return {"peer_id": args.id, "revoked": True, "note": "This peer can no longer access bridge tasks through direct TLS or legacy SSH. Remove any legacy public authorized_keys entry locally if desired."}
    if args.command == "project":
        if args.action == "list":
            return actions.send(state, actions.request("projects", {}), args.remote)
        require(not args.remote, "Project policy can be changed only by the local operator.", "permission_denied")
        identifier(args.id)
        if args.action == "register":
            require(args.repository is not None and args.verification is not None, "--repository and --verification are required.")
            require(args.verification.stat().st_size <= 16384, "Verification profile is too large.")
            return actions.register_project(state, args.id, args.repository, json.loads(args.verification.read_text()), args.agent)
        if args.action == "enable":
            return asyncio.run(actions.enable_project(state, args.id))
        config = load_config(state)
        require(args.id in config["projects"], "Project is not registered.")
        if args.action == "disable":
            config["projects"][args.id]["execution_enabled"] = False
            save_config(state, config)
            return {"project_id": args.id, "execution_enabled": False}
        require(not config["projects"][args.id]["execution_enabled"], "Disable project execution before local reconciliation.")
        store = Store(state)
        try:
            tasks = store.db.execute("SELECT id FROM tasks WHERE project=? AND status='interrupted'", (args.id,)).fetchall()
            for (task_id,) in tasks:
                task = store.get(task_id)
                if task["worker_pid"]:
                    try:
                        os.kill(task["worker_pid"], 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise BridgeError("reconciliation_required", "A recorded worker PID still exists. No process was killed or replacement started; inspect it locally first.")
                store.update(task_id, "locally_reconciled", status="failed", worker_pid=None, error="Local operator reconciled an interrupted attempt; workspace retained.")
            return {"project_id": args.id, "reconciled": len(tasks)}
        finally:
            store.close()
    if args.command == "task":
        require(not args.shared or args.action in {'list', 'conversation', 'status', 'events', 'wait'},
                '--shared is only available for task-history reads.')
        if args.action == "list":
            return actions.send(state, actions.request("tasks", {"cursor": args.cursor, **({'shared': True} if args.shared else {})}), args.remote)
        if args.action == "submit":
            if args.file:
                require(args.file.stat().st_size <= 120000, "Task file exceeds message limit.")
                body = decode(args.file.read_bytes())
            else:
                require(args.project is not None and args.objective is not None, "Provide --file or --project and --objective.")
                projects = actions.send(state, actions.request("projects", {}), args.remote)
                require(args.project in projects, "Project is not available to this identity.", "permission_denied")
                project = projects[args.project]
                body = {"project_id": args.project, "recipient": project["worker"], "base_commit": project["base_commit"],
                        "objective": args.objective, "scope": project["scope"], "acceptance": args.acceptance or ["Pass the locally configured verification profile."],
                        "context": [], "limits": project["limits"], "permission_profile": "proposal-v1"}
            return actions.send(state, actions.request("submit", body, args.key), args.remote)
        identifier(args.id)
        if args.action == "fetch":
            require(args.remote is not None, "Select an enrolled --remote for Git result transfer.")
            return actions.fetch_result(state, args.remote, args.id)
        if args.action == "wait":
            require(1 <= args.timeout <= 30, "Wait timeout must be between 1 and 30 seconds.")
            deadline = time.monotonic() + args.timeout
            while True:
                task = actions.send(state, actions.request("status", {"task_id": args.id, **({'shared': True} if args.shared else {})}), args.remote)
                if task["status"] not in {"queued", "running", "cancelling"} or time.monotonic() >= deadline:
                    return task
                time.sleep(0.5)
        body = {"task_id": args.id}
        if args.shared:
            body['shared'] = True
        if args.action in {"events", "conversation"}:
            body["cursor"] = args.cursor
        if args.action in {"answer", "revise"}:
            body.update({"revision": args.revision, "text": args.text})
            if args.action == "answer":
                body["question_id"] = args.question_id
        return actions.send(state, actions.request(args.action, body, args.key), args.remote)
