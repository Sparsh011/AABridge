from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_MESSAGE = 131_072
ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
OPERATIONS = frozenset({"submit", "status", "events", "conversation", "tasks", "answer", "revise", "cancel", "ping", "projects", "shutdown", "activity"})
TEAM_OPS = frozenset({"teams", "team_start", "team_status", "team_reply", "team_cancel", "team_retry", "team_activity",
                      "team_guide", "team_decide", "team_merge"})
OPERATIONS = OPERATIONS | TEAM_OPS
MUTATIONS = frozenset({"submit", "answer", "revise", "cancel", "team_start", "team_reply", "team_cancel", "team_retry",
                       "team_guide", "team_decide", "team_merge"})
APPROVALS = ("ask", "auto")
PLACEMENTS = ("sandbox", "branch", "worktree", "current")
DECISIONS = ("continue", "replan", "finish")


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def require(condition: bool, message: str, code: str = "invalid_request") -> None:
    if not condition:
        raise BridgeError(code, message)


def fields(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    require(isinstance(value, dict), "Expected an object.")
    require(required <= value.keys() <= required | optional, "Missing or unknown fields.")
    return value


def text(value: object, maximum: int = 4000, *, empty: bool = False) -> str:
    require(isinstance(value, str), "Expected text.")
    require((empty or bool(value.strip())) and len(value) <= maximum, "Text is empty or too long.")
    require(not any(ord(c) < 32 and c not in "\n\t\r" for c in value), "Control characters are not permitted.")
    return value


def identifier(value: object) -> str:
    require(isinstance(value, str) and ID.fullmatch(value) is not None, "Invalid identifier.")
    return value


def integer(value: object, low: int, high: int) -> int:
    require(type(value) is int and low <= value <= high, "Integer outside supported bounds.")
    return value


def relative_file(value: object) -> str:
    value = text(value, 200)
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value, "Expected a normalized repository-relative file.")
    require(all(part not in (".", "..") and not part.startswith(".") for part in path.parts), "Hidden or traversing paths are not supported.")
    require(re.fullmatch(r"[A-Za-z0-9_/-]+(?:\.[A-Za-z0-9_-]+)*", value) is not None, "Unsupported file name.")
    return value



def model_settings(value: object, agent: str) -> dict:
    value = fields(value, set(), {"model", "effort"})
    if "model" in value:
        require(isinstance(value["model"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,159}", value["model"]) is not None,
                "Enter a model ID or alias without spaces or command options.")
    if "effort" in value:
        supported = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"} if agent == "codex" else {"low", "medium", "high", "xhigh", "max", "ultracode"}
        require(isinstance(value["effort"], str) and value["effort"] in supported, "Unsupported effort setting for this agent.")
    return value

def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def decode(data: bytes) -> object:
    require(len(data) <= MAX_MESSAGE, "Message too large.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON object key.")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: require(False, "Non-finite JSON number."))
    except (ValueError, UnicodeError, RecursionError):
        raise BridgeError("invalid_request", "Malformed JSON.") from None


@dataclass(frozen=True)
class Request:
    op: str
    key: str
    body: dict

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical({"op": self.op, "body": self.body}).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {"version": 1, "op": self.op, "key": self.key, "body": self.body}


def validate_request(value: object) -> Request:
    envelope = fields(value, {"version", "op", "key", "body"})
    require(type(envelope["version"]) is int and envelope["version"] == 1, "Unsupported protocol version.")
    op = envelope["op"]
    require(isinstance(op, str) and op in OPERATIONS, "Unsupported operation.")
    key = identifier(envelope["key"])
    body = envelope["body"]
    if op == "team_start":
        fields(body, {"objective", "leader", "follower"}, {"approval", "placement", "max_fixes"})
        text(body["objective"], 3000)
        require(body.get("approval", "ask") in APPROVALS, "Choose whether the team asks you for approval.")
        if "max_fixes" in body:
            integer(body["max_fixes"], 1, 10)
        if "placement" in body:
            placement = fields(body["placement"], {"mode"}, {"pull_request"})
            require(placement["mode"] in PLACEMENTS, "Choose where the result goes.")
            require(type(placement.get("pull_request", False)) is bool, "Pull request must be true or false.")
            require(not placement.get("pull_request") or placement["mode"] != "sandbox", "A pull request needs a branch in your project.")
        for role in ("leader", "follower"):
            member = fields(body[role], {"target", "project", "agent"}, {"model_settings"})
            identifier(member["target"])
            identifier(member["project"])
            require(member["agent"] in {"codex", "claude"}, "Choose Codex or Claude.")
            if "model_settings" in member:
                model_settings(member["model_settings"], member["agent"])
        require(tuple(body["leader"][key] for key in ("target", "project", "agent")) != tuple(body["follower"][key] for key in ("target", "project", "agent")), "Choose different leader and follower participants.")
    elif op == "team_activity":
        fields(body, {"team_id", "cursor"})
        identifier(body["team_id"])
        integer(body["cursor"], 0, 2**63 - 1)
    elif op == "team_guide":
        fields(body, {"team_id", "text"})
        identifier(body["team_id"])
        text(body["text"], 3000)
    elif op == "team_decide":
        fields(body, {"team_id", "decision"}, {"text"})
        identifier(body["team_id"])
        require(body["decision"] in DECISIONS, "Unknown decision.")
        if "text" in body:
            text(body["text"], 3000)
    elif op in {"team_status", "team_cancel", "team_retry", "team_reply", "team_merge"}:
        fields(body, {"team_id"} | ({"text"} if op == "team_reply" else set()), {"leader_settings", "follower_settings"} if op == "team_reply" else set())
        identifier(body["team_id"])
        if op == "team_reply":
            text(body["text"], 3000)
            for role in ("leader", "follower"):
                if role + "_settings" in body:
                    fields(body[role + "_settings"], set(), {"model", "effort"})
    elif op == "submit":
        fields(body, {"project_id", "recipient", "base_commit", "objective", "scope", "acceptance", "context", "limits", "permission_profile"}, {"origin", "mode", "model_settings"})
        identifier(body["project_id"])
        identifier(body["recipient"])
        require(isinstance(body["base_commit"], str) and SHA.fullmatch(body["base_commit"]) is not None, "An exact base commit is required.")
        text(body["objective"])
        coding = body["permission_profile"] == "coding-v1"
        require(body.get("mode", "work") in {"work", "plan", "review"}, "Invalid coding role.")
        require(coding or "mode" not in body, "Roles require coding mode.")
        if "model_settings" in body:
            require(coding, "Model selection requires a full coding project.")
            model_settings(body["model_settings"], body["recipient"])
        for name, cap, item_cap in (("scope", 16, 200), ("acceptance", 8, 1000), ("context", 8, 1000)):
            items = body[name]
            require(isinstance(items, list) and len(items) <= cap, "List exceeds bounds.")
            require(name == "context" or len(items) > 0, "Scope and acceptance must not be empty.")
            for item in items:
                (require(item == "*", "Coding tasks use the whole project.") if coding else relative_file(item)) if name == "scope" else text(item, 100000 if coding and name == "context" else item_cap)
            require(len(items) == len(set(items)), "Duplicate list items.")
        limits = fields(body["limits"], {"runtime_seconds", "max_turns", "max_revisions"})
        integer(limits["runtime_seconds"], 10, 86400 if coding else 300)
        integer(limits["max_turns"], 1, 100 if coding else 6)
        integer(limits["max_revisions"], 0, 100 if coding else 3)
        require(body["permission_profile"] in {"proposal-v1", "coding-v1"}, "Unknown permission profile.", "permission_denied")
    elif op in {"status", "cancel"}:
        fields(body, {"task_id"})
        identifier(body["task_id"])
    elif op in {"events", "conversation", "activity"}:
        fields(body, {"task_id", "cursor"})
        identifier(body["task_id"])
        integer(body["cursor"], 0, 2**63 - 1)
    elif op == "tasks":
        fields(body, {"cursor"})
        integer(body["cursor"], 0, 2**63 - 1)
    elif op in {"answer", "revise"}:
        fields(body, {"task_id", "revision", "text"} | ({"question_id"} if op == "answer" else set()), {"origin", "mode", "model_settings"})
        identifier(body["task_id"])
        integer(body["revision"], 1, 101)
        text(body["text"], 100000)
        if "model_settings" in body:
            fields(body["model_settings"], set(), {"model", "effort"})
        if op == "answer":
            identifier(body["question_id"])
    else:
        fields(body, set())
    if op in {"submit", "answer", "revise"} and "origin" in body:
        origin = fields(body["origin"], {"kind", "name"})
        require(isinstance(origin["kind"], str) and origin["kind"] in {"human", "agent"}, "Invalid message origin kind.")
        text(origin["name"], 80)
    return Request(op, key, body)
