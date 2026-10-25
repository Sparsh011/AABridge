"""Bounded local/SSH activity and human task controls for the desktop app.

Imports the bundled wheel without installation. No model credential export,
remote enrollment, or generic command execution. Local project
configuration may check account/isolation without a model turn. Explicit
submit/answer/revise operations can start managed subscription model turns.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--action", choices=("targets", "projects", "projects-local", "project-save", "tasks", "conversation", "status", "events", "submit", "answer", "revise", "cancel"), required=True)
    parser.add_argument("--remote")
    parser.add_argument("--task-id")
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="Collect bounded history pages for the desktop viewer.")
    args = parser.parse_args()
    manifest = json.loads((args.bundle / "manifest.json").read_text())
    name = manifest["wheel"]
    if Path(name).name != name:
        raise ValueError("Invalid bundled wheel name.")
    import hashlib
    wheel = args.bundle / name
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != manifest["wheel_sha256"]:
        raise ValueError("Bundled bridge checksum mismatch.")
    sys.path.insert(0, str(wheel))
    from aabridge_cli.actions import request, send
    from aabridge_core.config import load_config
    from aabridge_preflight.paths import default_state_directory
    from aabridge_protocol.messages import BridgeError, MAX_MESSAGE, decode, fields, identifier, require
    state = args.state_dir or default_state_directory()
    if args.action in {"projects-local", "project-save"}:
        require(not args.remote, "Project permissions must be edited locally on the receiving Mac.", "permission_denied")
        if args.action == "project-save":
            import asyncio
            from aabridge_cli.actions import configure_file_project, enable_project, store_claude_token
            body = fields(decode(sys.stdin.buffer.read(MAX_MESSAGE + 1)), {"id", "repository", "scope", "peers"}, {"agent", "claude_token"})
            identifier(body["id"])
            require(isinstance(body["repository"], str), "Choose a repository folder.")
            agent = body.get("agent", "codex")
            require(agent in {"codex", "claude"}, "Choose Codex or Claude Code as the worker.")
            if body.get("claude_token"):
                require(isinstance(body["claude_token"], str), "Invalid Claude token.")
                store_claude_token(state, body["claude_token"])
            configure_file_project(state, body["id"], Path(body["repository"]), body["scope"], body["peers"], agent)
            try:
                asyncio.run(enable_project(state, body["id"]))
            except BridgeError as exc:
                raise BridgeError(exc.code, "Project scope saved, but execution remains disabled: " + exc.message) from None
        config = load_config(state)
        print(json.dumps({"claude_token_stored": (state / "claude-oauth-token").exists(),
              "projects": [{"id": name, **{key: value[key] for key in ("repository", "adapter", "scope", "verification", "execution_enabled")},
              "peers": [peer for peer, policy in config["peers"].items() if peer != "local" and policy["enabled"] and (name in policy["projects"] or "*" in policy["projects"])]}
              for name, value in config["projects"].items()],
              "peers": [name for name, policy in config["peers"].items() if name != "local" and policy["enabled"]]}))
        return
    if args.action == "targets":
        targets = [{"id": "local", "label": "This Mac"}]
        if (state / "config.json").exists():
            config = load_config(state)
            targets.extend({"id": name, "label": name + " · " + remote["host"]} for name, remote in config["remotes"].items())
        print(json.dumps({"targets": targets}))
        return
    if args.remote:
        identifier(args.remote)
    key = None
    if args.action in {"submit", "answer", "revise", "cancel"}:
        envelope = fields(decode(sys.stdin.buffer.read(MAX_MESSAGE + 1)), {"key", "body"})
        key = identifier(envelope["key"])
        require(isinstance(envelope["body"], dict), "Expected a task action body.")
        body = dict(envelope["body"])
        if args.action != "cancel":
            body["origin"] = {"kind": "human", "name": "You"}
    else:
        body = {"cursor": args.cursor} if args.action in {"tasks", "conversation", "events"} else {}
        if args.action not in {"tasks", "projects"}:
            body["task_id"] = identifier(args.task_id)
    try:
        result = send(state, request(args.action, body, key), args.remote)
        if args.action == "projects":
            result = {"projects": [{"id": name, **value} for name, value in result.items()]}
        if args.all and args.action in {"tasks", "conversation", "events"}:
            key = {"tasks": "tasks", "conversation": "messages", "events": "events"}[args.action]
            items = list(result[key])
            for _ in range(128):
                more = result.get("has_more", len(result[key]) == 64 if args.action == "events" else False)
                if not more:
                    break
                previous = result["cursor"]
                body["cursor"] = previous
                result = send(state, request(args.action, body), args.remote)
                if args.action == "events" and not result[key]:
                    break  # A full final event page has no has_more indicator.
                if result["cursor"] == previous:
                    raise ValueError("Receiver returned a history page without cursor progress.")
                items.extend(result[key])
            else:
                raise ValueError("History exceeds this viewer's page limit. Read remaining pages using the CLI cursor.")
            result[key] = items
    except BridgeError as exc:
        if exc.code == "invalid_request" and exc.message in {"Unsupported operation.", "Missing or unknown fields."}:
            raise ValueError("This receiver needs the conversation-history/chat-controls update. Open the new AABridge app on that Mac and select Install receiver. Existing pairing and task history are preserved.") from None
        raise
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "local_error"), "message": getattr(exc, "message", None) or (str(exc) if isinstance(exc, ValueError) else "Could not complete the bridge operation. Check the receiver, Remote Login, and the selected connection.")}}))
        raise SystemExit(1)
