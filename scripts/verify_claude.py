"""Opt-in check of the real installed Claude Code CLI inside the real proposal sandbox.

A local fake Messages API replaces api.anthropic.com, so this makes no model
calls and uses no subscription. It proves the sandboxed CLI starts, exposes only
its structured-output tool, returns a schema-valid question, and exactly resumes
the same session for the answer turn. It does not prove live authentication;
that needs a real `claude setup-token` token and a bounded live task.

Usage: .venv/bin/python scripts/verify_claude.py [--claude PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aabridge_adapter_claude.worker import ClaudeWorker
from aabridge_preflight.runtimes import locate_runtime

FAKE_TOKEN = "sk-ant-oat01-" + "f" * 40  # Never valid; the fake provider ignores it.
QUESTION = {"kind": "question", "question": "Should add perform integer addition?", "summary": "", "edits": [], "issues": []}
RESULT = {"kind": "result", "question": "", "summary": "Use integer addition.", "issues": [],
          "edits": [{"path": "math_ops.py", "content": "def add(a, b):\n    return a + b\n"}]}


class FakeProvider:
    """Minimal streaming Messages API: structured-output tool call, then end turn."""
    def __init__(self):
        self.requests = []
        self.server = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def handle(self, reader, writer):
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode().split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                headers = {key.lower(): value.strip() for key, value in (line.split(":", 1) for line in lines[1:] if line)}
                body = await reader.readexactly(int(headers.get("content-length", "0")))
                if method == "POST" and path.split("?")[0] == "/v1/messages":
                    await self.messages(json.loads(body), writer)
                else:
                    payload = b'{"type":"error","error":{"type":"not_found_error","message":"fixture"}}'
                    writer.write(b"HTTP/1.1 404 Not Found\r\ncontent-type: application/json\r\ncontent-length: %d\r\n\r\n%s" % (len(payload), payload))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    async def messages(self, request, writer):
        tools = [tool.get("name") for tool in request.get("tools", [])]
        last = request["messages"][-1]["content"]
        answered = isinstance(last, list) and any(block.get("type") == "tool_result" for block in last)
        if "StructuredOutput" in tools:
            self.requests.append({"tools": tools, "messages": len(request["messages"]), "text": json.dumps(request["messages"])})
        if "StructuredOutput" in tools and not answered:
            output = RESULT if len(self.requests) > 1 else QUESTION
            block = {"type": "tool_use", "id": f"toolu_{len(self.requests)}", "name": "StructuredOutput", "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(output)}
            stop = "tool_use"
        else:
            block, delta, stop = {"type": "text", "text": ""}, {"type": "text_delta", "text": "Done."}, "end_turn"
        message = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": request.get("model", "fixture"),
                   "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
        events = [("message_start", {"type": "message_start", "message": message}),
                  ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}}),
                  ("message_stop", {"type": "message_stop"})]
        if not request.get("stream"):
            content = [{**block, "input": json.loads(delta["partial_json"])} if stop == "tool_use" else {"type": "text", "text": "Done."}]
            payload = json.dumps({**message, "content": content, "stop_reason": stop}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: %d\r\n\r\n%s" % (len(payload), payload))
            return
        data = "".join(f"event: {name}\ndata: {json.dumps(value)}\n\n" for name, value in events).encode()
        writer.write(b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ncache-control: no-cache\r\ncontent-length: %d\r\n\r\n%s" % (len(data), data))


class FixturePort:
    """Stands in for the provider relay: the sandbox's one permitted port is the fixture."""
    secret = "unused"

    def __init__(self, port: int):
        self.port = port

    async def start(self) -> int:
        return self.port

    async def close(self):
        pass


class FixtureWorker(ClaudeWorker):
    """Verification only; never registered as a production adapter."""
    def __init__(self, *args, port):
        super().__init__(*args)
        self.proxy = FixturePort(port)

    def _environment(self, home, temporary, proxy_port, token):
        environment = super()._environment(home, temporary, proxy_port, token)
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            environment.pop(name, None)
        environment.update(ANTHROPIC_BASE_URL=f"http://127.0.0.1:{proxy_port}", NO_PROXY="127.0.0.1")
        return environment


async def turn(state, task, project, port, checkpoints):
    worker = FixtureWorker(state, task, project, lambda *ids: checkpoints.append(ids), lambda pid: None, port=port)
    try:
        return await asyncio.wait_for(worker.turn({"math_ops.py": "def add(a, b):\n    return a - b\n"}), 120)
    finally:
        await worker.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", help="Claude Code executable or claude.app; defaults to local discovery.")
    args = parser.parse_args()
    executable = locate_runtime("claude", args.claude)
    with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-claude-") as name:
        state = Path(name).resolve()
        state.chmod(0o700)
        (state / "workers").mkdir(mode=0o700)
        token = state / "token"
        token.write_text(FAKE_TOKEN)
        token.chmod(0o600)
        project = {"claude_executable": str(executable), "claude_token_file": str(token)}
        task = {"id": "task-verify", "attempt_id": "attempt-verify", "revision": 1, "thread_id": None,
                "objective": "Fix add.", "acceptance": ["Pass"], "context": [], "scope": ["math_ops.py"], "pending": None}
        preflight = ClaudeWorker(state, {"id": "preflight-verify"}, project, lambda *ids: None, lambda pid: None)
        try:
            await preflight.start()
        finally:
            await preflight.close()
        provider = FakeProvider()
        port = await provider.start()
        checkpoints = []
        try:
            first = await turn(state, task, project, port, checkpoints)
            assert first == QUESTION, first
            session = checkpoints[0][0]
            task.update(thread_id=session, pending={"kind": "answer", "text": "Yes, return a + b."})
            second = await turn(state, task, project, port, checkpoints)
            assert second == RESULT, second
        finally:
            await provider.close()
        assert checkpoints == [(session, session), (session, session)], checkpoints
        # Claude Code also sends a reserved no-op placeholder name; the worker rejects
        # any tool call other than StructuredOutput, so neither can act.
        offered = sorted({tool for request in provider.requests for tool in request["tools"]})
        assert set(offered) <= {"StructuredOutput", "DeferredToolPlaceholder"}, offered
        resumed = provider.requests[-1]
        assert resumed["messages"] > 1 and "Should add perform integer addition?" in resumed["text"], "Resume did not restore the saved session."
        print(json.dumps({"claude_executable": str(executable), "sandbox_preflight": "passed", "session_id": session,
                          "question_turn": "passed", "exact_resume_turn": "passed", "tools_offered": offered,
                          "provider": "local fixture; no model calls"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
