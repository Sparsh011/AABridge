from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_fixture import select_fake_codex
from aabridge_cli.actions import demo_init, enroll_remote, request
from aabridge_adapter_codex.proxy import ProviderProxy
from aabridge_adapter_codex.worker import CodexWorker, validate_proposal
from aabridge_core.config import initialize, load_config, read_private, save_config, token_digest, write_private
from aabridge_daemon.server import Supervisor
from aabridge_mcp.server import handle, tools
from aabridge_protocol.messages import BridgeError, canonical
from aabridge_transport_local.client import call
from aabridge_transport_ssh.client import call as ssh_call, ssh_arguments


class DeterministicWorker:
    """Test fixture, never registered or selectable through production RPC."""
    def __init__(self, state, task, project, on_session, on_pid):
        self.task, self.on_session = task, on_session

    async def turn(self, sources):
        self.on_session(self.task["thread_id"] or "thread-" + self.task["id"], "exact-session")
        if self.task["turns"] == 1:
            return {"kind": "question", "question": "Should add perform addition?", "edits": [], "summary": "Need clarification", "issues": []}
        return {"kind": "result", "question": "", "summary": "Fixed addition", "issues": [],
                "edits": [{"path": "math_ops.py", "content": 'def add(a, b):\n    """Add two integers."""\n    return a + b\n'}]}

    async def close(self):
        pass


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-")
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        initialize(self.state)
        select_fake_codex(self.state)
        demo_init(self.state, self.root / "source", "demo")
        config = load_config(self.state)
        config["projects"]["demo"]["execution_enabled"] = True
        save_config(self.state, config)
        self.supervisor = Supervisor(self.state, worker_factory=DeterministicWorker)
        self.supervisor.isolation_checked = True  # fixture-only; kernel probes run separately
        self.server = await asyncio.start_unix_server(self.supervisor.accept, str(self.state / "bridge.sock"))
        (self.state / "bridge.sock").chmod(0o600)
        self.schedule = asyncio.create_task(self.supervisor.schedule())

    async def asyncTearDown(self):
        self.supervisor.stopping.set()
        await self.schedule
        self.server.close()
        await self.server.wait_closed()
        for connection in tuple(self.supervisor.connections):
            connection.cancel()
        await asyncio.gather(*tuple(self.supervisor.connections), return_exceptions=True)
        self.supervisor.store.close()
        self.temp.cleanup()

    async def call(self, op, body, key=None):
        return await asyncio.to_thread(call, self.state, request(op, body, key))

    async def until(self, task_id, status):
        for _ in range(100):
            task = await self.call("status", {"task_id": task_id})
            if task["status"] == status:
                return task
            if task["status"] == "failed":
                self.fail(str(task))
            await asyncio.sleep(0.03)
        self.fail("Task did not reach " + status)

    async def test_full_local_question_answer_revision_and_reconnect(self):
        projects = await self.call("projects", {})
        project = projects["demo"]
        body = {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
                "objective": "Fix addition", "scope": project["scope"], "acceptance": ["Pass profile"],
                "context": [], "limits": project["limits"], "permission_profile": "proposal-v1"}
        task = await self.call("submit", body, "submission-one")
        waiting = await self.until(task["id"], "waiting_question")
        self.assertIsNotNone(waiting["thread_id"])
        # Every call opens/closes IPC. Inference stopped at the persisted question.
        self.assertEqual(waiting["turns"], 1)
        await asyncio.sleep(0.1)
        self.assertEqual((await self.call("status", {"task_id": task["id"]}))["turns"], 1)
        await self.call("answer", {"task_id": task["id"], "revision": 1, "question_id": waiting["question"]["id"], "text": "Yes, addition"}, "answer-one")
        done = await self.until(task["id"], "completed")
        self.assertEqual(done["thread_id"], waiting["thread_id"])
        self.assertEqual(done["result"]["verification"][0]["exit_code"], 0)
        await self.call("revise", {"task_id": task["id"], "revision": 1, "text": "Keep the docstring concise"}, "revision-one")
        revised = await self.until(task["id"], "completed")
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["thread_id"], waiting["thread_id"])
        history = await self.call("conversation", {"task_id": task["id"], "cursor": 0})
        self.assertTrue(any(m["kind"] == "question" and m["text"] == "Should add perform addition?" for m in history["messages"]))
        self.assertTrue(any(m["kind"] == "answer" and m["text"] == "Yes, addition" for m in history["messages"]))
        self.assertTrue(any(m["kind"] == "prepared_model_input" and 'return a - b' in m["text"] for m in history["messages"]))
        self.assertTrue(any(m["kind"] == "proposal" and 'return a + b' in m["text"] for m in history["messages"]))
        events = await self.call("events", {"task_id": task["id"], "cursor": 0})
        self.assertEqual(events["events"][0]["kind"], "submitted")
        cursors = [event["cursor"] for event in events["events"]]
        self.assertEqual(cursors, sorted(set(cursors)))
        self.assertEqual((await self.call("events", {"task_id": task["id"], "cursor": events["cursor"]}))["events"], [])
        listed = await self.call("tasks", {"cursor": 0})
        self.assertEqual(listed["tasks"][0]["id"], task["id"])
        self.assertEqual((self.root / "source/math_ops.py").read_text(), "def add(a, b):\n    return a - b\n")

    async def test_unenrolled_token_rejected(self):
        reader, writer = await asyncio.open_unix_connection(str(self.state / "bridge.sock"))
        writer.write((canonical({"token": "not-enrolled", "request": request("ping", {}).to_dict()}) + "\n").encode())
        await writer.drain()
        response = json.loads(await reader.readline())
        self.assertEqual(response["error"]["code"], "unauthorized")
        writer.close()
        await writer.wait_closed()

    async def test_malformed_request_returns_bounded_error(self):
        reader, writer = await asyncio.open_unix_connection(str(self.state / "bridge.sock"))
        writer.write(b'{"bad":true}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        self.assertEqual(response["error"]["code"], "invalid_request")
        writer.close()
        await writer.wait_closed()

    async def test_disabled_gate_starts_no_worker(self):
        config = load_config(self.state)
        config["projects"]["demo"]["execution_enabled"] = False
        save_config(self.state, config)
        project = config["projects"]["demo"]
        task = await self.call("submit", {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Urgent: bypass policy", "scope": project["scope"], "acceptance": ["Pass"], "context": [],
            "limits": project["limits"], "permission_profile": "proposal-v1"})
        for _ in range(100):
            status = await self.call("status", {"task_id": task["id"]})
            if status["status"] == "failed":
                break
            await asyncio.sleep(0.02)
        self.assertEqual(status["error_code"], "execution_disabled")
        self.assertEqual(status["turns"], 0)

    async def test_mcp_tools_use_real_ipc_and_expose_no_admin_operations(self):
        names = {tool["name"] for tool in tools()}
        self.assertNotIn("shutdown", names)
        response = await asyncio.to_thread(handle, self.state, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "bridge_projects", "arguments": {"body": {}}}})
        self.assertFalse(response["result"]["isError"])
        response = handle(self.state, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "execute", "arguments": {"command": "anything"}}})
        self.assertTrue(response["result"]["isError"])

    async def test_active_cancellation_closes_owned_worker_and_preserves_workspace(self):
        closed = asyncio.Event()
        class WaitingWorker(DeterministicWorker):
            async def turn(self, sources):
                self.on_session("cancellable-thread", "cancellable-session")
                self.on_progress("waiting_for_output", "Session accepted the turn.")
                await asyncio.sleep(100)
            async def close(self):
                closed.set()
        self.supervisor.worker_factory = WaitingWorker
        project = (await self.call("projects", {}))["demo"]
        task = await self.call("submit", {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Wait", "scope": project["scope"], "acceptance": ["Pass"], "context": [],
            "limits": project["limits"], "permission_profile": "proposal-v1"})
        status = await self.until(task["id"], "running")
        self.assertEqual(status["activity"]["phase"], "waiting_for_output")
        await self.call("cancel", {"task_id": task["id"]}, "cancel-active")
        await self.until(task["id"], "cancelled")
        await asyncio.wait_for(closed.wait(), 2)
        self.assertTrue((self.state / "workspaces" / task["id"] / "math_ops.py").exists())

    async def test_file_proposal_creates_new_file_through_service_with_review_label(self):
        class FileWorker(DeterministicWorker):
            async def turn(self, sources):
                assert sources == {"hello.txt": None}
                self.on_session("file-thread", "file-session")
                return {"kind": "result", "question": "", "summary": "Created the requested empty file", "issues": [], "edits": [{"path": "hello.txt", "content": ""}]}
        self.supervisor.worker_factory = FileWorker
        config = load_config(self.state)
        project = config["projects"]["demo"]
        project["scope"] = ["hello.txt"]
        project["verification"] = {"profile": "file-proposal-v1", "files": ["hello.txt"]}
        save_config(self.state, config)
        task = await self.call("submit", {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Create hello.txt", "scope": ["hello.txt"], "acceptance": ["Review file"], "context": [],
            "limits": project["limits"], "permission_profile": "proposal-v1"})
        status = await self.until(task["id"], "completed")
        self.assertTrue(status["result"]["verification"][0]["review_required"])
        self.assertFalse((Path(project["repository"]) / "hello.txt").exists())
        self.assertTrue((self.state / "workspaces" / task["id"] / "hello.txt").is_file())
        history = await self.call("conversation", {"task_id": task["id"], "cursor": 0})
        self.assertEqual(history["messages"][-1]["kind"], "review_required_result")

    async def test_worker_failure_returns_no_provider_diagnostics(self):
        class FailingWorker(DeterministicWorker):
            async def turn(self, sources):
                raise RuntimeError("sensitive-provider-output-must-not-escape")
        self.supervisor.worker_factory = FailingWorker
        project = (await self.call("projects", {}))["demo"]
        task = await self.call("submit", {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Fail", "scope": project["scope"], "acceptance": ["Pass"], "context": [],
            "limits": project["limits"], "permission_profile": "proposal-v1"})
        for _ in range(100):
            status = await self.call("status", {"task_id": task["id"]})
            if status["status"] == "failed":
                break
            await asyncio.sleep(.02)
        self.assertEqual(status["status"], "failed")
        self.assertNotIn("sensitive-provider", json.dumps(status))
        self.assertIsNone(status["worker_pid"])

    async def test_same_thread_cannot_be_assigned_to_two_tasks(self):
        class SharedThreadWorker(DeterministicWorker):
            async def turn(self, sources):
                self.on_session("shared-thread", "shared-session")
                return {"kind": "question", "question": "Proceed?", "summary": "", "edits": [], "issues": []}
        self.supervisor.worker_factory = SharedThreadWorker
        project = (await self.call("projects", {}))["demo"]
        body = {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"],
            "objective": "Question", "scope": project["scope"], "acceptance": ["Pass"], "context": [],
            "limits": project["limits"], "permission_profile": "proposal-v1"}
        first = await self.call("submit", body, "first-thread-owner")
        await self.until(first["id"], "waiting_question")
        second = await self.call("submit", body, "second-thread-owner")
        for _ in range(100):
            status = await self.call("status", {"task_id": second["id"]})
            if status["status"] == "failed":
                break
            await asyncio.sleep(.02)
        self.assertEqual(status["error_code"], "worker_protocol_error")
        self.assertIsNone(status["thread_id"])

    async def test_mcp_stdio_handshake_lists_complete_schemas(self):
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}]
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "aabridge_cli", "mcp", "--state-dir", str(self.state)],
            input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        submit = next(t for t in responses[1]["result"]["tools"] if t["name"] == "bridge_submit")
        self.assertIn("base_commit", submit["inputSchema"]["properties"]["body"]["required"])
        self.assertEqual(result.stderr, "")

    async def test_mcp_empty_mutation_key_does_not_generate_a_new_request(self):
        response = handle(self.state, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "bridge_cancel", "arguments": {"key": "", "body": {"task_id": "task-does-not-exist"}}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Invalid identifier", response["result"]["content"][0]["text"])


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.proxy = ProviderProxy()
        self.port = await self.proxy.start()

    async def asyncTearDown(self):
        await self.proxy.close()

    async def exchange(self, destination, credential=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        header = f"CONNECT {destination} HTTP/1.1\r\nHost: {destination}\r\n"
        if credential:
            header += f"Proxy-Authorization: {credential}\r\n"
        writer.write((header + "\r\n").encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readline(), 2)
        writer.close()
        await writer.wait_closed()
        return response

    async def test_unapproved_destination_denied_even_with_valid_proxy_auth(self):
        response = await self.exchange("example.com:443", self.proxy.expected)
        self.assertIn(b"403", response)

    async def test_missing_and_wrong_proxy_auth_denied(self):
        for credential in (None, "Basic wrong"):
            self.assertIn(b"407", await self.exchange("chatgpt.com:443", credential))

    async def test_public_ip_and_loopback_connect_targets_denied(self):
        for destination in ("1.1.1.1:443", "127.0.0.1:22", "chatgpt.com:80"):
            self.assertIn(b"403", await self.exchange(destination, self.proxy.expected))

    async def test_shutdown_closes_idle_accepted_connection_before_waiting_for_server(self):
        # Emulate asyncio versions whose Server.wait_closed waits for accepted
        # transports. No provider connection or model is needed for this case.
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        for _ in range(100):
            if self.proxy.connections:
                break
            await asyncio.sleep(0.001)
        self.assertTrue(self.proxy.connections)
        server = self.proxy.server
        original_wait = server.wait_closed
        async def wait_for_clients():
            while self.proxy.connections:
                await asyncio.sleep(0.001)
            await original_wait()
        try:
            with patch.object(server, "wait_closed", side_effect=wait_for_clients):
                await asyncio.wait_for(self.proxy.close(), 1)
            self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")
            self.assertFalse(self.proxy.connections)
        finally:
            writer.close()
            await writer.wait_closed()


class SshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.identity = root / "identity"
        self.known = root / "known"
        write_private(self.identity, b"fixture-only-not-a-real-key")
        write_private(self.known, b"fixture-host-key")
        self.remote = {"host": "mini.local", "user": "bridge", "port": 22,
                       "identity_file": str(self.identity), "known_hosts": str(self.known)}

    def test_transport_pins_identity_disables_configs_and_uses_no_shell(self):
        result = subprocess.CompletedProcess([], 0, b'{"result":{"version":1}}', b"")
        with patch("aabridge_transport_ssh.client.subprocess.run", return_value=result) as run:
            self.assertEqual(ssh_call(self.remote, request("ping", {})), {"version": 1})
        argv = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("IdentityAgent=none", argv)
        self.assertEqual(argv[-1], "aabridge-rpc-v1")
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertIn("/dev/null", argv)

    def test_host_identity_mismatch_is_not_retried_or_silently_trusted(self):
        result = subprocess.CompletedProcess([], 255, b"", b"REMOTE HOST IDENTIFICATION HAS CHANGED")
        with patch("aabridge_transport_ssh.client.subprocess.run", return_value=result), self.assertRaises(BridgeError) as raised:
            ssh_call(self.remote, request("ping", {}))
        self.assertEqual(raised.exception.code, "host_identity_mismatch")

    def test_remote_host_shell_syntax_rejected(self):
        self.remote["host"] = "mini; unwanted-command"
        with self.assertRaises(BridgeError):
            ssh_arguments(self.remote)

    def test_publicly_readable_private_key_rejected(self):
        self.identity.chmod(0o644)
        with self.assertRaises(BridgeError):
            ssh_arguments(self.remote)

    def test_known_host_path_with_spaces_remains_one_ssh_config_value(self):
        directory = self.known.parent / "Application Support"
        directory.mkdir(mode=0o700)
        known = directory / "known-hosts"
        write_private(known, b"fixture-host-key")
        self.remote["known_hosts"] = str(known)
        self.assertIn(f'UserKnownHostsFile="{known}"', ssh_arguments(self.remote))


class AdapterWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_shutdown_escalates_only_its_owned_process_group(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as name:
            state = Path(name).resolve()
            (state / "workers").mkdir(mode=0o700)
            checkpoints = []
            worker = CodexWorker(state, {"id": "task-close"}, {}, lambda *args: None, checkpoints.append)
            worker.process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-c",
                "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(30)",
                stdout=asyncio.subprocess.PIPE, start_new_session=True)
            try:
                await asyncio.wait_for(worker.process.stdout.readline(), 3)
                await asyncio.wait_for(worker.close(), 4)
                self.assertEqual(worker.process.returncode, -9)
                self.assertEqual(checkpoints, [None])
            finally:
                if worker.process.returncode is None:
                    worker.process.kill()
                    await worker.process.wait()

    async def test_worker_permission_request_is_denied_without_self_approval(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as name:
            state = Path(name).resolve()
            (state / "workers").mkdir(mode=0o700)
            worker = CodexWorker(state, {"id": "task-permission"}, {}, lambda *args: None, lambda pid: None)
            reader = asyncio.StreamReader()
            reader.feed_data(b'{"method":"item/commandExecution/requestApproval","id":7,"params":{}}\n')
            class Process:
                stdout = reader
            worker.process = Process()
            sent = []
            async def capture(value):
                sent.append(value)
            with patch.object(worker, "_send", side_effect=capture), self.assertRaises(BridgeError) as raised:
                await worker._read()
            self.assertEqual(raised.exception.code, "permission_denied")
            self.assertEqual(sent, [{"id": 7, "result": {"decision": "decline"}}])

    async def test_exact_thread_resume_and_completed_result_validation(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as name:
            state = Path(name).resolve()
            (state / "workers").mkdir(mode=0o700)
            checkpoints = []
            task = {"id": "task-wire", "attempt_id": "attempt-wire", "revision": 1, "thread_id": "specific-thread",
                    "objective": "Fix addition", "acceptance": ["Pass"], "context": [], "scope": ["math_ops.py"], "pending": {"kind": "answer", "text": "yes"}}
            worker = CodexWorker(state, task, {}, lambda *args: checkpoints.append(args), lambda pid: None)
            progress = []
            worker.on_progress = lambda phase, message: progress.append((phase, message))
            calls = []
            async def start():
                pass
            async def protocol(method, params):
                calls.append((method, params))
                if method == "thread/resume":
                    return {"thread": {"id": "specific-thread", "sessionId": "session-original"}}
                worker.notifications = [
                    {"method": "thread/tokenUsage/updated", "params": {"threadId": "specific-thread", "turnId": "previous-turn"}},
                    {"method": "item/started", "params": {"threadId": "specific-thread", "turnId": "turn-one", "item": {"type": "reasoning", "text": "private reasoning must not be forwarded"}}},
                    {"method": "error", "params": {"threadId": "specific-thread", "turnId": "turn-one", "willRetry": True, "error": {"message": "Transient provider connection"}}},
                    {"method": "item/completed", "params": {"threadId": "specific-thread", "turnId": "turn-one", "item": {"type": "agentMessage", "text": json.dumps({"kind": "result", "question": "", "summary": "Done", "edits": [], "issues": []})}}},
                    {"method": "turn/completed", "params": {"threadId": "specific-thread", "turn": {"id": "turn-one", "status": "completed"}}},
                ]
                return {"turn": {"id": "turn-one"}}
            with patch.object(worker, "start", side_effect=start), patch.object(worker, "_request", side_effect=protocol):
                result = await worker.turn({"math_ops.py": "def add(a,b): return a-b"})
            self.assertEqual(result["kind"], "result")
            self.assertEqual(calls[0][0], "thread/resume")
            self.assertEqual(calls[0][1]["threadId"], "specific-thread")
            self.assertEqual(checkpoints, [("specific-thread", "session-original")])
            self.assertEqual([phase for phase, _ in progress], ["connecting", "resuming", "waiting_for_output", "working", "retrying"])
            self.assertNotIn("private reasoning", json.dumps(progress))

    async def test_question_may_have_empty_summary_but_result_may_not(self):
        question = {"kind": "question", "question": "Should add perform integer addition?", "summary": "", "issues": [], "edits": []}
        self.assertEqual(validate_proposal(question)["kind"], "question")
        with self.assertRaises(BridgeError):
            validate_proposal({**question, "kind": "result", "question": ""})
