from __future__ import annotations

import json
import sys
from importlib.resources import files
from pathlib import Path

from aabridge_protocol.messages import BridgeError, MUTATIONS, decode, identifier, require

TOOL_OPS = {"bridge_projects": "projects", "bridge_submit": "submit", "bridge_status": "status",
            "bridge_tasks": "tasks", "bridge_conversation": "conversation",
            "bridge_teams": "teams", "bridge_team_start": "team_start", "bridge_team_status": "team_status", "bridge_team_activity": "team_activity", "bridge_activity": "activity",
            "bridge_team_reply": "team_reply", "bridge_team_cancel": "team_cancel", "bridge_team_retry": "team_retry",
            "bridge_events": "events", "bridge_answer": "answer", "bridge_revise": "revise", "bridge_cancel": "cancel"}


def tools() -> list[dict]:
    contract = json.loads(files("aabridge_protocol").joinpath("schemas/v1/request.schema.json").read_text())
    bodies = {variant["properties"]["op"]["const"]: variant["properties"]["body"] for variant in contract["oneOf"]}
    descriptions = {
        "projects": "List only projects assigned to this coordinator identity, with approved baseline, scope and limits.",
        "submit": "Submit a proposal-v1 or locally enabled coding-v1 task; returns its durable task ID promptly. Supply a stable unique key for safe retries.",
        "status": "Read task status, exact session IDs, pending question or independently checked result.",
        "events": "Retrieve ordered events after body.cursor (initially 0); use returned cursor to resume.",
        "tasks": "Browse authorized tasks, newest first, with pagination cursor initially 0. This does not start inference.",
        "conversation": "Read permanently saved messages and supplied context for a task after body.cursor, initially 0. Follow has_more and cursor.",
        "answer": "Answer body.question_id at body.revision for body.task_id. Stale/cross-task answers are rejected.",
        "revise": "Request a revision of a completed task at its current revision, subject to local limits.",
        "cancel": "Cancel only the identified task; preserve its partial workspace. Closing MCP does not cancel tasks.",
    }
    descriptions["activity"] = "Read public agent activity after body.cursor. Includes tools, file changes, and progress messages; excludes private reasoning and raw tool output."
    descriptions.update({
        "team_activity": "Read saved live leader/follower activity after body.cursor. Reading does not start model work.",
        "teams": "List team chats owned by the local admin. Device roles are chosen per room.",
        "team_start": "Create a team assignment explicitly authorized by the human admin. Leader plans and reviews; follower implements. Requires a local admin connection and full coding projects on both participants.",
        "team_status": "Read the saved team conversation, stage, errors, and implementation branch.",
        "team_reply": "Send the admin's next task to a completed/stopped team. This starts model work.",
        "team_cancel": "Stop a team and its active task; preserve the checkout.",
        "team_retry": "Retry a blocked stage using its existing durable request key.",
    })
    result = []
    for name, op in TOOL_OPS.items():
        result.append({"name": name, "description": descriptions[op], "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"body": bodies[op], "key": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,79}$"}, "remote": {"type": "string", "description": "An already enrolled local remote name. Omit for this host."}},
            "required": ["body", "key"] if op in MUTATIONS else ["body"],
        }})
    return result


def handle(state: Path, value: dict) -> dict | None:
    from aabridge_cli.actions import request, send
    require(isinstance(value, dict) and value.get("jsonrpc") == "2.0", "Invalid JSON-RPC envelope.")
    method = value.get("method")
    if "id" not in value:
        return None
    identity = value["id"]
    params = value.get("params", {})
    require(type(identity) in (int, str) and isinstance(params, dict), "Invalid JSON-RPC identifier or parameters.")
    if method == "initialize":
        protocol = params.get("protocolVersion", "2025-11-25")
        if protocol not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
            protocol = "2025-11-25"
        result = {"protocolVersion": protocol, "capabilities": {"tools": {}}, "serverInfo": {"name": "aabridge", "version": "0.2.0"},
                  "instructions": "Use bridge_projects first. Tasks persist beyond this connection. No tool grants policy, shell, enrollment, merge or deployment authority."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools()}
    elif method == "tools/call":
        try:
            name = params.get("name")
            require(name in TOOL_OPS, "Unsupported bridge tool.", "permission_denied")
            arguments = params.get("arguments", {})
            require(isinstance(arguments, dict) and set(arguments) <= {"body", "key", "remote"}, "Unknown tool arguments.")
            op = TOOL_OPS[name]
            if op in MUTATIONS:
                identifier(arguments.get("key"))
            if "remote" in arguments:
                identifier(arguments["remote"])
            output = send(state, request(op, arguments.get("body"), arguments.get("key")), arguments.get("remote"))
            result = {"content": [{"type": "text", "text": json.dumps(output)}], "isError": False}
        except BridgeError as exc:
            result = {"content": [{"type": "text", "text": json.dumps({"error": {"code": exc.code, "message": exc.message}})}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": identity, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": identity, "result": result}


def run(state: Path):
    initialized = False
    while line := sys.stdin.buffer.readline(131073):
        message_id = None
        try:
            require(len(line) <= 131072 and line.endswith(b"\n"), "MCP message exceeds limit.")
            message = decode(line)
            message_id = message.get("id") if isinstance(message, dict) else None
            require(initialized or message.get("method") == "initialize", "Initialize MCP before calling tools.")
            response = handle(state, message)
            if message.get("method") == "initialize":
                initialized = True
        except BridgeError as exc:
            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32600, "message": exc.message}}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            response = {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32603, "message": "Bridge tool failed; raw diagnostics omitted."}}
        if response is not None:
            print(json.dumps(response), flush=True)
