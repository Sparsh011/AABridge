"""Bounded local/direct TLS activity and human task controls for the desktop app.

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
    parser.add_argument("--action", choices=("connection-card", "connect", "teams", "team_start", "team_status", "team_activity", "team_reply", "team_cancel", "team_retry", "team_guide", "team_decide", "team_merge", "targets", "projects", "projects-local", "project-save", "tasks", "conversation", "status", "events", "submit", "answer", "revise", "cancel", "task_rename", "task_forget", "task_work"), required=True)
    parser.add_argument("--remote")
    parser.add_argument("--task-id")
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="Collect bounded history pages for the desktop viewer.")
    parser.add_argument('--shared', action='store_true', help='Read receiver-local coding history in granted projects.')
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
    from aabridge_protocol.messages import HISTORY_READS
    require(not args.shared or args.action in HISTORY_READS, 'Shared history is read-only.')
    state = args.state_dir or default_state_directory()
    if args.action in {"connection-card", "connect"}:
        require(not args.remote, "Connections belong to this Mac.")
        from aabridge_core.pairing import card, connect
        if args.action == "connection-card":
            raw = sys.stdin.buffer.read(MAX_MESSAGE + 1)
            options = fields(decode(raw), set(), {'host', 'port'}) if raw.strip() else {}
            require(isinstance(options.get('host', ''), str), 'Enter a LAN/VPN address or leave it blank.')
            print(json.dumps(card(state, host=options.get('host', ''), port=options.get('port'))))
        else:
            raw_card = sys.stdin.buffer.read(MAX_MESSAGE + 1)
            require(not raw_card.lstrip().startswith(b"ssh "),
                    "Paste an AABridge connection card, not an SSH command. On the other Mac, choose Create connection card → Copy card.")
            print(json.dumps(connect(state, decode(raw_card))))
        return
    if args.action.startswith("team"):
        require(not args.remote, "Manage team roles on the admin Mac.")
        if args.action == "teams":
            body, key = {}, None
        else:
            envelope = fields(decode(sys.stdin.buffer.read(MAX_MESSAGE + 1)), {"key", "body"})
            body, key = envelope["body"], envelope["key"]
        print(json.dumps(send(state, request(args.action, dict(body), key))))
        return
    if args.action in {"projects-local", "project-save"}:
        require(not args.remote, "Project permissions must be edited locally on the receiving Mac.", "permission_denied")
        if args.action == "project-save":
            import asyncio
            from aabridge_cli.actions import configure_file_project, enable_project, store_claude_token
            body = fields(decode(sys.stdin.buffer.read(MAX_MESSAGE + 1)), {"id", "repository", "scope", "peers"}, {"agent", "claude_token", "coding", "links"})
            identifier(body["id"])
            require(isinstance(body["repository"], str), "Choose a repository folder.")
            agent = body.get("agent", "codex")
            require(agent in {"codex", "claude"}, "Choose Codex or Claude Code as the worker.")
            if body.get("claude_token"):
                require(isinstance(body["claude_token"], str), "Invalid Claude token.")
                store_claude_token(state, body["claude_token"])
            if body.get("coding") is True:
                from aabridge_cli.actions import configure_coding_project
                configure_coding_project(state, body["id"], Path(body["repository"]), body["peers"], agent, body.get("links", []))
            else:
                configure_file_project(state, body["id"], Path(body["repository"]), body["scope"], body["peers"], agent)
            try:
                asyncio.run(enable_project(state, body["id"]))
            except BridgeError as exc:
                raise BridgeError(exc.code, "Project scope saved, but execution remains disabled: " + exc.message) from None
        config = load_config(state)
        print(json.dumps({"peer_labels": {name: policy.get("label", name) for name, policy in config["peers"].items()}, "claude_token_stored": (state / "claude-oauth-token").exists(),
              "projects": [{"id": name, **{key: value[key] for key in ("repository", "adapter", "scope", "verification", "execution_enabled")}, "links": value.get("links", []),
              "peers": [peer for peer, policy in config["peers"].items() if peer != "local" and policy["enabled"] and (name in policy["projects"] or "*" in policy["projects"])]}
              for name, value in config["projects"].items()],
              "peers": [name for name, policy in config["peers"].items() if name != "local" and policy["enabled"]]}))
        return
    if args.action == "targets":
        targets = [{"id": "local", "label": "This Mac"}]
        if (state / "config.json").exists():
            config = load_config(state)
            targets.extend({"id": name, "label": remote.get("label", name)} for name, remote in config["remotes"].items())
        print(json.dumps({"targets": targets}))
        return
    if args.remote:
        identifier(args.remote)
    key = None
    if args.action in {"submit", "answer", "revise", "cancel", "task_rename", "task_forget"}:
        envelope = fields(decode(sys.stdin.buffer.read(MAX_MESSAGE + 1)), {"key", "body"})
        key = identifier(envelope["key"])
        require(isinstance(envelope["body"], dict), "Expected a task action body.")
        body = dict(envelope["body"])
        if args.action in {"submit", "answer", "revise"}:
            body["origin"] = {"kind": "human", "name": "You"}
    else:
        body = {"cursor": args.cursor} if args.action in {"tasks", "conversation", "events"} else {}
        if args.action not in {"tasks", "projects"}:
            body["task_id"] = identifier(args.task_id)
    if args.shared:
        body['shared'] = True
    history_notice = ''
    def read():
        nonlocal history_notice
        try:
            return send(state, request(args.action, dict(body), key), args.remote)
        except BridgeError as exc:
            if body.get('shared') and exc.code == 'invalid_request' and exc.message == 'Missing or unknown fields.':
                # Older receivers still expose owned history. Never label that
                # subset as a successfully synchronized full history.
                body.pop('shared')
                label = load_config(state)['remotes'].get(args.remote, {}).get('label') or args.remote or 'this Mac'
                history_notice = f'Showing tasks submitted through this connection only. Update AABridge on {label} to include tasks created on that Mac.'
                return send(state, request(args.action, dict(body), key), args.remote)
            raise
    try:
        result = read()
        if args.action == 'tasks' and args.shared and result.get('capabilities', {}).get('shared_task_history') is not True:
            history_notice = history_notice or 'This receiver has not enabled shared task history. Update AABridge on the selected Mac; this list contains only tasks submitted through this connection.'
        if args.action == "projects":
            result = {"projects": [{"id": name, **value} for name, value in result.items()]}
        if args.all and args.action in {"tasks", "conversation", "events"}:
            page_key = {"tasks": "tasks", "conversation": "messages", "events": "events"}[args.action]
            items = list(result[page_key])
            for _ in range(128):
                more = result.get("has_more", len(result[page_key]) == 64 if args.action == "events" else False)
                if not more:
                    break
                previous = result["cursor"]
                body["cursor"] = previous
                result = read()
                if args.action == "events" and not result[page_key]:
                    break  # A full final event page has no has_more indicator.
                if result["cursor"] == previous:
                    raise ValueError("Receiver returned a history page without cursor progress.")
                items.extend(result[page_key])
            else:
                raise ValueError("History exceeds this viewer's page limit. Read remaining pages using the CLI cursor.")
            result[page_key] = items
        if args.action == 'tasks':
            result['tasks'].sort(key=lambda task: (task.get('updated_at', 0), task.get('id', '')), reverse=True)
        if history_notice:
            result['history_notice'] = history_notice
    except BridgeError as exc:
        if exc.code == "invalid_request" and exc.message in {"Unsupported operation.", "Missing or unknown fields."}:
            raise ValueError("This receiver needs the conversation-history/chat-controls update. Open the new AABridge app on that Mac and select Install receiver. Existing pairing and task history are preserved.") from None
        raise
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "local_error"), "message": getattr(exc, "message", None) or (str(exc) if isinstance(exc, ValueError) else "Could not complete the bridge operation. Check that AABridge is running on the other Mac and the selected LAN/VPN address is reachable.")}}))
        raise SystemExit(1)
