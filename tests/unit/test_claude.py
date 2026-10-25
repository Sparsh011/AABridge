from __future__ import annotations

import asyncio
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_adapter_claude.worker import ClaudeWorker, validate_token
from aabridge_adapter_codex.isolation import sandbox_profile
from aabridge_cli.actions import CLAUDE_TOKEN, request, store_claude_token
from aabridge_core.config import initialize, load_config, save_config
from aabridge_daemon import server
from aabridge_preflight.runtimes import locate_runtime
from aabridge_protocol.messages import BridgeError
from aabridge_transport_local.client import call

SESSION = "6f1c1b7e-1111-4222-8333-444455556666"
TOKEN = "sk-ant-oat01-" + "a" * 40
RESULT = {"kind": "result", "question": "", "summary": "Done", "edits": [], "issues": []}


def init(session=SESSION, **extra):
    return {"type": "system", "subtype": "init", "session_id": session, "tools": ["StructuredOutput"],
            "mcp_servers": [], "apiKeySource": "none", **extra}


def assistant(*content, session=SESSION, **extra):
    return {"type": "assistant", "session_id": session, "message": {"content": list(content)}, **extra}


def result(session=SESSION, **extra):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": session, "structured_output": RESULT, **extra}


class FakeInput:
    def __init__(self):
        self.data, self.closed = b"", False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class ClaudeWireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-")
        self.state = Path(self.temp.name).resolve()
        (self.state / "workers").mkdir(mode=0o700)
        token = self.state / "token"
        token.write_text(TOKEN)
        token.chmod(0o600)
        self.executable = self.state / "claude"
        self.executable.write_text("fixture only; must never execute")
        self.executable.chmod(0o755)
        self.project = {"claude_executable": str(self.executable), "claude_token_file": str(token)}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def run_turn(self, events, thread_id=None):
        task = {"id": "task-claude", "attempt_id": "attempt-claude", "revision": 1, "thread_id": thread_id,
                "objective": "Fix addition", "acceptance": ["Pass"], "context": [], "scope": ["math_ops.py"], "pending": None}
        self.checkpoints, self.progress, self.launched = [], [], []
        worker = ClaudeWorker(self.state, task, self.project, lambda *args: self.checkpoints.append(args), lambda pid: None)
        worker.on_progress = lambda phase, message: self.progress.append(phase)
        stdin = FakeInput()

        async def launch(executable, arguments, token):
            self.launched.append((arguments, token))
            reader = asyncio.StreamReader()
            for event in events:
                reader.feed_data((json.dumps(event) + "\n").encode())
            reader.feed_eof()

            class Process:
                stdout, returncode = reader, 0
            Process.stdin = stdin
            worker.process = Process()
        with patch.object(worker, "_launch", side_effect=launch):
            try:
                return await worker.turn({"math_ops.py": "def add(a, b):\n    return a - b\n"})
            finally:
                self.stdin = stdin

    async def test_first_turn_pins_new_session_and_returns_structured_result(self):
        events = [{"type": "rate_limit_event"}, init(),
                  assistant({"type": "thinking", "thinking": "private reasoning must not be forwarded"}),
                  assistant({"type": "tool_use", "name": "StructuredOutput", "input": RESULT}),
                  {"type": "user", "session_id": SESSION, "message": {"content": []}}, result()]
        with patch("aabridge_adapter_claude.worker.uuid.uuid4", return_value=SESSION):
            outcome = await self.run_turn(events)
        self.assertEqual(outcome, RESULT)
        arguments, token = self.launched[0]
        self.assertEqual(token, TOKEN)
        self.assertEqual(arguments[arguments.index("--session-id") + 1], SESSION)
        self.assertNotIn("--resume", arguments)
        self.assertEqual(arguments[arguments.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", arguments)
        self.assertEqual(self.checkpoints, [(SESSION, SESSION)])
        self.assertEqual(self.progress, ["connecting", "starting_session", "waiting_for_output", "working", "preparing_response"])
        self.assertNotIn("private reasoning", json.dumps(self.progress))
        self.assertTrue(self.stdin.closed)
        self.assertEqual(json.loads(self.stdin.data)["task_id"], "task-claude")

    async def test_resume_uses_only_saved_session(self):
        outcome = await self.run_turn([init(), result()], thread_id=SESSION)
        self.assertEqual(outcome["kind"], "result")
        arguments = self.launched[0][0]
        self.assertEqual(arguments[arguments.index("--resume") + 1], SESSION)
        self.assertNotIn("--session-id", arguments)

    async def test_resumed_session_mismatch_rejected(self):
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init(session="00000000-0000-4000-8000-000000000000"), result()], thread_id=SESSION)
        self.assertEqual(raised.exception.code, "worker_protocol_error")
        self.assertEqual(self.checkpoints, [])

    async def test_other_tool_use_rejected(self):
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init(), assistant({"type": "tool_use", "name": "Bash", "input": {"command": "id"}}), result()], thread_id=SESSION)
        self.assertEqual(raised.exception.code, "permission_denied")

    async def test_effective_tools_and_mcp_must_stay_disabled(self):
        for event in (init(tools=["StructuredOutput", "Read"]), init(mcp_servers=[{"name": "x"}])):
            with self.assertRaises(BridgeError) as raised:
                await self.run_turn([event, result()], thread_id=SESSION)
            self.assertEqual(raised.exception.code, "permission_denied")

    async def test_api_key_billing_is_rejected(self):
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init(apiKeySource="ANTHROPIC_API_KEY"), result()], thread_id=SESSION)
        self.assertEqual(raised.exception.code, "authentication_failed")

    async def test_permission_prompt_is_denied(self):
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init(), {"type": "control_request", "session_id": SESSION, "request": {"subtype": "can_use_tool"}}], thread_id=SESSION)
        self.assertEqual(raised.exception.code, "permission_denied")

    async def test_provider_error_has_bounded_category_and_no_raw_text(self):
        events = [init(), assistant({"type": "text", "text": "secret provider detail"}, error="authentication_failed"),
                  result(is_error=True, structured_output=None)]
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn(events, thread_id=SESSION)
        self.assertEqual(raised.exception.code, "worker_failed")
        self.assertIn("authentication_failed", raised.exception.message)
        self.assertNotIn("secret", raised.exception.message)

    async def test_exit_without_result_and_invalid_proposal_fail_closed(self):
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init()], thread_id=SESSION)
        self.assertEqual(raised.exception.code, "worker_failed")
        bad = {**RESULT, "kind": "question", "question": "Approve?", "edits": [{"path": "x", "content": "y"}]}
        with self.assertRaises(BridgeError):
            await self.run_turn([init(), result(structured_output=bad)], thread_id=SESSION)

    async def test_changed_binary_is_denied(self):
        self.project["claude_sha256"] = "0" * 64
        with self.assertRaises(BridgeError) as raised:
            await self.run_turn([init(), result()])
        self.assertEqual(raised.exception.code, "adapter_changed")


class ClaudeTokenTests(unittest.TestCase):
    def test_only_subscription_tokens_are_accepted(self):
        self.assertEqual(validate_token(" " + TOKEN + "\n"), TOKEN)
        for value in ("sk-ant-api03-" + "a" * 40, "", "sk-ant-oat01-short", TOKEN + " extra"):
            with self.assertRaises(BridgeError):
                validate_token(value)

    def test_token_is_stored_private_and_removable(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-") as name:
            state = Path(name).resolve() / "state"
            initialize(state)
            report = store_claude_token(state, TOKEN + "\n")
            self.assertNotIn(TOKEN, json.dumps(report))
            path = state / CLAUDE_TOKEN
            self.assertEqual(path.read_text(), TOKEN)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(store_claude_token(state, None), {"claude_token": "removed"})
            self.assertFalse(path.exists())

    def test_sandbox_profile_grants_only_named_system_roots(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-") as name:
            runtime = Path(name).resolve()
            executable = runtime / "bin/claude"
            profile = sandbox_profile(runtime, executable, proxy_port=4000, preference_domain="com.anthropic.claudecode",
                                      extra_roots=("/private/var/db/timezone",))
            self.assertIn('(subpath "/private/var/db/timezone")', profile)
            self.assertIn('(preference-domain "com.anthropic.claudecode")', profile)
            self.assertNotIn("com.openai.codex", profile)
            with self.assertRaises(BridgeError):
                sandbox_profile(runtime, executable, extra_roots=("/Users",))


class ClaudeDiscoveryTests(unittest.TestCase):
    def test_desktop_bundled_claude_found_and_selectable_as_app(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-") as name:
            root = Path(name).resolve()
            application = root / "claude-code/2.1.0/abc123/claude.app"
            executable = application / "Contents/MacOS/claude"
            executable.parent.mkdir(parents=True)
            executable.write_text("fixture only; must never execute")
            executable.chmod(0o755)
            with patch("aabridge_preflight.runtimes.shutil.which", return_value=None):
                found = locate_runtime("claude", command_roots=(), desktop_roots=(root / "claude-code",))
            self.assertEqual(found, executable)
            self.assertEqual(locate_runtime("claude", str(application)), executable)
            second = root / "claude-code/2.2.0/def456/claude.app/Contents/MacOS/claude"
            second.parent.mkdir(parents=True)
            second.write_text("fixture only; must never execute")
            second.chmod(0o755)
            with patch("aabridge_preflight.runtimes.shutil.which", return_value=None), self.assertRaises(ValueError):
                locate_runtime("claude", command_roots=(), desktop_roots=(root / "claude-code",))


class ClaudeRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_routes_claude_projects_to_claude_adapter(self):
        self.assertIs(server.WORKERS["claude"], ClaudeWorker)
        used = []

        class Fixture:
            """Test fixture, never registered or selectable through production RPC."""
            def __init__(self, state, task, project, on_session, on_pid):
                used.append(project["adapter"])
                self.on_session = on_session

            async def turn(self, sources):
                self.on_session(SESSION, SESSION)
                return {**RESULT, "edits": [{"path": "math_ops.py", "content": "def add(a, b):\n    return a + b\n"}]}

            async def close(self):
                pass
        with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-") as name:
            root = Path(name).resolve()
            state = root / "state"
            initialize(state)
            source = root / "source"
            source.mkdir()
            (source / "math_ops.py").write_text("def add(a, b):\n    return a - b\n")
            for args in (("init", "--initial-branch=main", "--template="), ("add", "math_ops.py"),
                         ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "base")):
                subprocess.run(["/usr/bin/git", "-C", str(source), *args], check=True, capture_output=True)
            from aabridge_workspace.git import git
            base = git(source, "rev-parse", "HEAD")
            config = load_config(state)
            config["projects"]["claude-demo"] = {
                "repository": str(source), "adapter": "claude", "worker": "claude", "scope": ["math_ops.py"], "base_commit": base,
                "verification": {"profile": "integer-function-v1", "file": "math_ops.py", "function": "add",
                                 "cases": [{"args": [2, 3], "expected": 5}]},
                "execution_enabled": True, "limits": {"runtime_seconds": 60, "max_turns": 4, "max_revisions": 1}}
            save_config(state, config)
            supervisor = server.Supervisor(state)
            supervisor.isolation_checked = True  # fixture-only; kernel probes run separately
            listener = await asyncio.start_unix_server(supervisor.accept, str(state / "bridge.sock"))
            (state / "bridge.sock").chmod(0o600)
            schedule = asyncio.create_task(supervisor.schedule())
            try:
                with patch.dict(server.WORKERS, {"claude": Fixture}):
                    body = {"project_id": "claude-demo", "recipient": "claude", "base_commit": base, "objective": "Fix add",
                            "scope": ["math_ops.py"], "acceptance": ["Pass"], "context": [],
                            "limits": {"runtime_seconds": 60, "max_turns": 4, "max_revisions": 1}, "permission_profile": "proposal-v1"}
                    task = await asyncio.to_thread(call, state, request("submit", body))
                    for _ in range(200):
                        task = await asyncio.to_thread(call, state, request("status", {"task_id": task["id"]}))
                        if task["status"] in {"completed", "failed"}:
                            break
                        await asyncio.sleep(0.03)
                self.assertEqual(task["status"], "completed", task.get("error"))
                self.assertEqual(task["recipient"], "claude")
                self.assertEqual(task["thread_id"], SESSION)
                self.assertEqual(used, ["claude"])
            finally:
                supervisor.stopping.set()
                await schedule
                listener.close()
                await listener.wait_closed()
                supervisor.store.close()


if __name__ == "__main__":
    unittest.main()
