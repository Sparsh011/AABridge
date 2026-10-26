"""Public CLI activity only: never persist reasoning or raw tool arguments/output."""
from __future__ import annotations

import re
import time


def clean(value, maximum=2000):
    if not isinstance(value, str):
        return ""
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    value = re.sub(r"(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|Bearer\s+\S+)", "[redacted]", value, flags=re.I)
    value = re.sub(r"(?i)(api[_-]?key|access[_-]?token|password|secret)(\s*[:=]\s*)([^\s,;]+)", r"\1\2[redacted]", value)
    return value[:maximum]


class ActivityStream:
    def __init__(self, callback):
        self.callback = callback
        self.text = {}
        self.sent = {}
        self.block = None
        self.message = ""
        self.tools = {}

    def emit(self, key, kind, text, state="running", *, force=False):
        text = clean(text)
        signature = (kind, text, state)
        previous, last = self.sent.get(key, (None, 0))
        now = time.monotonic()
        if signature == previous or (not force and state == "running" and now - last < .5):
            return
        self.sent[key] = (signature, now)
        self.callback({"id": clean(str(key), 200), "kind": kind, "text": text, "state": state})

    def codex(self, event):
        typ = event.get("type")
        if typ == "turn.started":
            self.emit("turn", "status", "Agent session started", force=True)
        if typ == "turn.completed":
            self.emit("turn", "status", "Agent finished this turn", "completed", force=True)
        if typ in {"error", "turn.failed"}:
            self.emit("turn", "status", "Agent stopped before completing. Check the chosen model and its availability.", "failed", force=True)
        if typ not in {"item.started", "item.updated", "item.completed"}:
            return
        item = event.get("item") or {}
        key, kind = item.get("id", "current"), item.get("type")
        done = typ == "item.completed"
        state = "failed" if item.get("status") == "failed" else "completed" if done else "running"
        if kind == "agent_message":
            self.emit(key, "message", item.get("text", ""), state, force=done)
        elif kind == "reasoning":
            # The reasoning payload can contain private chain of thought.
            self.emit(key, "status", "Working through the task", state, force=done)
        elif kind == "command_execution":
            code = item.get("exit_code")
            if done and type(code) is int and code != 0:
                state = "failed"
            label = "Running a project command" if not done else "Project command finished" + (" · exit " + str(code) if type(code) is int else "")
            self.emit(key, "tool", label, state, force=True)
        elif kind == "file_change":
            paths = [clean(c.get("path", ""), 180) for c in item.get("changes", [])[:8] if isinstance(c, dict)]
            self.emit(key, "file", ("Changed files" if done else "Editing files") + (": " + ", ".join(paths) if paths else ""), state, force=True)
        elif kind in {"mcp_tool_call", "web_search", "plan", "todo_list"}:
            label = {"mcp_tool_call": "Using a connected tool", "web_search": "Searching the web", "plan": "Updating the plan", "todo_list": "Updating the task list"}[kind]
            self.emit(key, "tool", label, state, force=True)

    def claude(self, event):
        typ = event.get("type")
        if typ == "system":
            subtype = event.get("subtype")
            if subtype == "init":
                model = clean(event.get("model", ""), 160)
                self.emit("session", "status", "Agent session started" + (" · model: " + model if model else ""), force=True)
            elif subtype in {"api_retry", "status"}:
                self.emit("service", "status", "Retrying the connection" if subtype == "api_retry" else "Agent is processing the task", force=True)
        elif typ == "tool_progress":
            elapsed = event.get("elapsed_time_seconds")
            label = self.tools.get(event.get("tool_use_id"), "Tool is running")
            if isinstance(elapsed, (int, float)) and 0 <= elapsed < 86400:
                label += " · " + str(int(elapsed)) + "s"
            self.emit(event.get("tool_use_id", "tool"), "tool", label)
        elif typ == "stream_event":
            stream = event.get("event") or {}
            if stream.get("type") == "message_start":
                self.message = (stream.get("message") or {}).get("id", "current")
            elif stream.get("type") == "content_block_start":
                block = stream.get("content_block") or {}
                key = self.message + ":" + str(stream.get("index", 0))
                self.block = (key, block.get("type"))
                if block.get("type") == "text":
                    self.text[key] = clean(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    self.tool(block)
                elif block.get("type") in {"thinking", "redacted_thinking"}:
                    self.emit(key, "status", "Working through the task", force=True)
            elif stream.get("type") == "content_block_delta" and self.block:
                key, kind = self.block
                delta = stream.get("delta") or {}
                if kind == "text" and delta.get("type") == "text_delta":
                    self.text[key] = clean(self.text.get(key, "") + delta.get("text", ""))
                    self.emit(key, "message", self.text[key])
                # Ignore thinking_delta, signature_delta and input_json_delta.
            elif stream.get("type") == "content_block_stop" and self.block:
                key, kind = self.block
                if kind == "text":
                    self.emit(key, "message", self.text.get(key, ""), "completed", force=True)
                self.block = None
        elif typ == "assistant":
            message = event.get("message") or {}
            for index, block in enumerate(message.get("content", [])):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    self.emit(str(message.get("id", "current")) + ":" + str(index), "message", block.get("text", ""), "completed", force=True)
                elif block.get("type") == "tool_use":
                    self.tool(block)
                elif block.get("type") in {"thinking", "redacted_thinking"}:
                    self.emit(str(message.get("id", "current")) + ":thinking", "status", "Working through the task", force=True)
        elif typ == "user":
            for block in (event.get("message") or {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self.emit(block.get("tool_use_id", "tool"), "tool", (self.tools.get(block.get("tool_use_id"), "Tool") + " · finished") if not block.get("is_error") else "Tool reported an error", "failed" if block.get("is_error") else "completed", force=True)
        elif typ == "result":
            self.emit("turn", "status", "Agent finished this turn" if event.get("is_error") is False else "Agent stopped before completing. Check the chosen model and its availability.", "completed" if event.get("is_error") is False else "failed", force=True)

    def tool(self, block):
        name = clean(block.get("name", "Tool"), 60)
        label = {"Read": "Reading a file", "Edit": "Editing a file", "Write": "Writing a file", "Glob": "Finding files", "Grep": "Searching project files", "Bash": "Running a project command"}.get(name, "Using tool: " + name)
        # Deliberately omit command text, tool input and raw output; they may
        # contain credentials or file contents unrelated to progress.
        path = (block.get("input") or {}).get("file_path") if name in {"Read", "Edit", "Write"} else None
        if isinstance(path, str):
            label += ": " + clean(path, 200)
        self.tools[block.get("id", name)] = label
        self.emit(block.get("id", name), "tool", label, force=True)
