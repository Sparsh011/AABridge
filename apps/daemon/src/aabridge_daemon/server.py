from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import stat
import time
from pathlib import Path

from aabridge_adapter_codex.isolation import probe_isolation
from aabridge_adapter_claude.worker import ClaudeWorker
from aabridge_adapter_codex.worker import CodexWorker
from aabridge_core.config import authenticate, authorize, load_config, private_directory, read_private, write_private
from aabridge_core.store import Store, new_id
from aabridge_protocol.messages import BridgeError, MAX_MESSAGE, canonical, decode, fields, require, validate_request
from aabridge_workspace.git import Workspace


WORKERS = {"codex": CodexWorker, "claude": ClaudeWorker}


class Supervisor:
    def __init__(self, state: Path, *, worker_factory=None):
        self.state = private_directory(state)
        self.store = Store(self.state)
        # None selects each project's locally registered adapter. Tests inject fixtures.
        self.worker_factory = worker_factory
        self.stopping = asyncio.Event()
        self.active = None
        self.active_id = None
        self.connections = set()
        self.isolation_checked = False

    async def accept(self, reader, writer):
        connection = asyncio.current_task()
        self.connections.add(connection)
        try:
            require(len(self.connections) <= 16, "Too many IPC connections.", "rate_limited")
            data = await asyncio.wait_for(reader.readline(), 5)
            envelope = fields(decode(data), {"token", "request"})
            config = load_config(self.state)
            identity = authenticate(config, envelope["token"])
            request = validate_request(envelope["request"])
            result = self.store.handle(request, identity, config)
            # Commit happened before acknowledging any task mutation.
            response = {"result": result}
            if request.op == "shutdown":
                self.stopping.set()
        except BridgeError as exc:
            response = {"error": {"code": exc.code, "message": exc.message}}
        except (ValueError, OSError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            response = {"error": {"code": "invalid_request", "message": "Invalid, oversized, or timed-out IPC request."}}
        except Exception:
            response = {"error": {"code": "internal_error", "message": "Request failed; no raw logs or secrets were returned."}}
        try:
            encoded = (canonical(response) + "\n").encode()
            if len(encoded) > 262144:
                encoded = b'{"error":{"code":"response_too_large","message":"Response exceeds limit."}}\n'
            writer.write(encoded)
            await writer.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            writer.close()
            self.connections.discard(connection)

    async def run_task(self, task: dict):
        worker = None
        started = time.monotonic()
        try:
            config = load_config(self.state)
            project = authorize(config, task["sender"], task["project_id"])
            require(project["adapter"] in WORKERS, "No enabled adapter exists for this runtime.", "adapter_unavailable")
            require(set(task["scope"]) <= set(project["scope"]), "Task scope no longer authorized.", "permission_denied")
            require(task["base_commit"] == project["base_commit"], "Project baseline changed. Start a new task using its current scope and baseline.", "permission_denied")
            require(project.get("execution_enabled") is True, "Local execution gate is disabled. Run the local project-enable preflight.", "execution_disabled")
            require(task["turns"] < task["limits"]["max_turns"], "Task turn limit reached.", "limit_exceeded")
            remaining = task["limits"]["runtime_seconds"] - task["runtime_used"]
            require(remaining > 1, "Task runtime budget exhausted.", "limit_exceeded")
            if not self.isolation_checked:
                probe_isolation()
                self.isolation_checked = True
            task = self.store.update(task["id"], "worker_started", status="running", turns=task["turns"] + 1,
                                     verification_profile=project["verification"], activity={"phase": "preparing", "message": "Preparing the scoped task checkout.", "time": time.time()})
            workspace = Workspace(self.state, task, project)
            sources = workspace.prepare()
            from aabridge_adapter_codex.worker import INSTRUCTIONS, model_input
            self.store.record_message(task["id"], "context", "prepared_model_input",
                                      json.dumps({"developer_instructions": INSTRUCTIONS, "input": model_input(task, sources)}, indent=2))
            def on_session(thread_id, session_id):
                require(isinstance(thread_id, str) and isinstance(session_id, str) and 1 <= len(thread_id) <= 128 and 1 <= len(session_id) <= 128,
                        "Invalid provider session identifiers.")
                existing = self.store.get(task["id"])
                require(existing["thread_id"] in (None, thread_id) and existing["session_id"] in (None, session_id),
                        "Worker changed the task's saved session identity.", "worker_protocol_error")
                for (document,) in self.store.db.execute("SELECT document FROM tasks WHERE id != ?", (task["id"],)):
                    other = json.loads(document)
                    require(other["thread_id"] != thread_id, "Provider thread is already assigned to another task.", "worker_protocol_error")
                self.store.update(task["id"], "session_checkpoint", thread_id=thread_id, session_id=session_id)
            def on_pid(pid):
                self.store.update(task["id"], "worker_process", worker_pid=pid)
            worker = (self.worker_factory or WORKERS[project["adapter"]])(self.state, task, project, on_session, on_pid)
            last_phase = [None]
            def on_progress(phase, message):
                if phase != last_phase[0]:
                    last_phase[0] = phase
                    self.store.update(task["id"], "worker_activity", activity={"phase": phase, "message": message, "time": time.time()})
            worker.on_progress = on_progress
            proposal = await asyncio.wait_for(worker.turn(sources), remaining)
            current = self.store.get(task["id"])
            require(current["status"] != "cancelling", "Task was cancelled.", "cancelled")
            # Recheck local grants after inference and before applying any edit.
            latest = load_config(self.state)
            current_project = authorize(latest, task["sender"], task["project_id"])
            require(current_project == project, "Project policy changed during the attempt.", "permission_denied")
            if proposal["kind"] == "question":
                self.store.update(task["id"], "question", status="waiting_question",
                                  question={"id": new_id("question"), "text": proposal["question"], "revision": task["revision"], "attempt_id": task["attempt_id"]}, pending=None)
            else:
                self.store.record_message(task["id"], "worker", "proposal", json.dumps(proposal, indent=2))
                self.store.update(task["id"], "worker_activity", activity={"phase": "checking_proposal", "message": "Checking file scope and the project's verification profile.", "time": time.time()})
                result = workspace.result(proposal)
                self.store.update(task["id"], "result", status="completed", result=result, pending=None)
        except asyncio.CancelledError:
            status = "interrupted" if self.stopping.is_set() else "cancelled"
            self.store.update(task["id"], status, status=status)
        except asyncio.TimeoutError:
            self.store.update(task["id"], "limit_exceeded", status="failed", error="Task runtime limit reached; partial workspace preserved.")
        except BridgeError as exc:
            self.store.update(task["id"], "failed", status="failed", error_code=exc.code, error=exc.message)
        except Exception:
            self.store.update(task["id"], "failed", status="failed", error_code="worker_failed", error="Worker failed; partial work preserved. Raw provider output was not exposed.")
        finally:
            if worker:
                await worker.close()
            current = self.store.get(task["id"])
            self.store.update(task["id"], "attempt_closed", runtime_used=current["runtime_used"] + time.monotonic() - started)

    async def schedule(self):
        self.store.recover()
        while not self.stopping.is_set():
            if self.active and self.active.done():
                await self.active
                self.active = self.active_id = None
            if self.active:
                task = self.store.get(self.active_id)
                config = load_config(self.state)
                peer = config["peers"].get(task["sender"], {})
                if task["status"] == "cancelling" or not peer.get("enabled") or not config["projects"].get(task["project_id"], {}).get("execution_enabled"):
                    self.active.cancel()
            else:
                for task in self.store.queued():
                    if not self.store.project_interrupted(task["project_id"]):
                        self.active_id = task["id"]
                        self.active = asyncio.create_task(self.run_task(task))
                        break
            await asyncio.sleep(0.1)
        if self.active:
            self.active.cancel()
            await asyncio.gather(self.active, return_exceptions=True)


async def serve(state: Path):
    state = private_directory(state)
    os.umask(0o077)
    lock_path = state / "daemon.lock"
    if not lock_path.exists():
        write_private(lock_path, b"", exclusive=True)
    read_private(lock_path, 32)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError("already_running", "A supervisor already owns this state directory.") from None
        endpoint = state / "bridge.sock"
        require(len(str(endpoint).encode()) < 104, "State path is too long for a macOS Unix socket. Choose a shorter private state directory.")
        if endpoint.exists() or endpoint.is_symlink():
            info = endpoint.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                    "Refusing to replace an unsafe socket path.", "unsafe_state")
            endpoint.unlink()  # Only this verified stale IPC endpoint, under the daemon lock.
        supervisor = Supervisor(state)
        server = await asyncio.start_unix_server(supervisor.accept, str(endpoint), limit=MAX_MESSAGE)
        endpoint.chmod(0o600)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, supervisor.stopping.set)
        try:
            async with server:
                await supervisor.schedule()
        finally:
            for connection in tuple(supervisor.connections):
                connection.cancel()
            await asyncio.gather(*tuple(supervisor.connections), return_exceptions=True)
            supervisor.store.close()
    finally:
        os.close(descriptor)
