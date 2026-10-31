"""Exercise the actual native chat controls over authenticated IPC.

This is a control-plane fixture. No supervisor worker scheduler is started, so
no agent process or model inference can run. A committed submit acknowledgement
is deliberately dropped to verify durable idempotent retry after app restart.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from aabridge_cli.actions import demo_init, request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_daemon.server import Supervisor


async def verify(args):
    root = Path(tempfile.mkdtemp(prefix="aab-chat-", dir="/private/tmp")).resolve()
    state = root / "state"
    initialize(state)
    demo_init(state, root / "source", "demo")
    config = load_config(state)
    config["projects"]["demo"]["execution_enabled"] = True
    config["projects"]["files"] = {**config["projects"]["demo"], "scope": ["math_ops.py", "notes/hello.txt"],
        "verification": {"profile": "file-proposal-v1", "files": ["math_ops.py", "notes/hello.txt"]}}
    save_config(state, config)
    project = config["projects"]["demo"]
    body = {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"], "scope": project["scope"],
            "acceptance": ["Pass local profile"], "context": [], "limits": project["limits"], "permission_profile": "proposal-v1",
            "origin": {"kind": "human", "name": "You"}}
    store = Store(state)
    waiting = store.handle(request("submit", {**body, "objective": "Chat-control verification fixture: fix integer addition."}), "local", config)
    store.record_message(waiting["id"], "system", "verification_notice", "Control-plane verification data. No worker scheduler or model is running in this test.")
    store.update(waiting["id"], "question", status="waiting_question", question={"id": "question-control-test", "text": "Verification fixture question: should add return the sum of two integers?"})
    completed = store.handle(request("submit", {**body, "objective": "Completed control-plane fixture for native revision testing."}), "local", config)
    store.update(completed["id"], "result", status="completed", result={"summary": "Verification fixture result; no model inference."})
    running = store.handle(request("submit", {**body, "objective": "Activity display verification fixture; no model worker runs."}), "local", config)
    store.update(running["id"], "worker_started", status="running", activity={"phase": "waiting_for_output", "message": "Verification fixture for the active indicator; no actual worker is running.", "time": __import__("time").time()})
    store.close()
    supervisor = Supervisor(state)
    dropped = []

    class DropAcknowledgement:
        def __init__(self, writer):
            self.writer, self.closed_early = writer, False

        def write(self, data):
            value = json.loads(data).get("result", {})
            if not dropped and value.get("objective") == "Native chat-control verification task; no model worker is scheduled.":
                dropped.append(value["id"])
                self.closed_early = True
                self.writer.close()
            else:
                self.writer.write(data)

        async def drain(self):
            if not self.closed_early:
                await self.writer.drain()

        def close(self):
            self.writer.close()

    async def accept(reader, writer):
        await supervisor.accept(reader, DropAcknowledgement(writer))

    server = await asyncio.start_unix_server(accept, str(state / "bridge.sock"))
    (state / "bridge.sock").chmod(0o600)
    try:
        result = await asyncio.to_thread(subprocess.run,
            [str(args.app.resolve() / "Contents/MacOS/AABridge"), "--verify-chat", str(state), str(args.preview.resolve())],
            text=True, capture_output=True, timeout=160)
        print(result.stdout)
        if result.returncode:
            print(result.stderr[-2000:])
            raise RuntimeError("Native chat-control verification failed.")
        assert len(dropped) == 1
        preference = supervisor.store.db.execute('SELECT title,hidden FROM task_preferences WHERE task_id=? AND viewer=?', (waiting['id'], 'local')).fetchone()
        assert preference == ('Fix addition', 1), 'Native rename/removal was not persisted'
        assert supervisor.store.history_page(waiting['id'], 0)['messages'], 'History removal erased durable task records'

        tasks = [json.loads(row[0]) for row in supervisor.store.db.execute("SELECT document FROM tasks")]
        assert len(tasks) == 4, "A lost acknowledgement caused a duplicate submission"
        submitted = next(task for task in tasks if task["id"] == dropped[0])
        assert submitted["project_id"] == "files" and submitted["scope"] == ["notes/hello.txt"]
        assert supervisor.store.get(waiting["id"])["status"] == "cancelled"
        assert supervisor.store.get(completed["id"])["revision"] == 2
        history = supervisor.store.history_page(waiting["id"], 0)["messages"]
        answer = next(item for item in history if item["kind"] == "answer")
        assert answer["actor"] == {"kind": "human", "name": "You"}
        assert not (root / "desktop-outbox/pending-action.json").exists()
        print(json.dumps({"passed": True, "category": "native GUI controls and authenticated IPC without a worker scheduler",
            "checks": ["title edit", "history removal preserves task records", "human sender persisted", "answer", "revision", "cancel", "new task", "editable scope subset", "immediate pending bubble", "activity display", "local project GUI model", "dropped acknowledgement", "restored private outbox", "same-key retry without duplicate", "silent refresh and draft preservation"],
            "state": str(state), "preview": str(args.preview.resolve()), "model_inference_started": False}, indent=2))
    finally:
        # Leave no runnable fixture tasks or enabled execution behind.
        for policy in config["projects"].values():
            policy["execution_enabled"] = False
        save_config(state, config)
        for task in supervisor.store.queued() + [supervisor.store.get(running["id"])]:
            supervisor.store.handle(request("cancel", {"task_id": task["id"]}), "local", config)
        supervisor.store.update(running["id"], "cancelled", status="cancelled")
        server.close()
        for connection in tuple(supervisor.connections):
            connection.cancel()
        await asyncio.gather(*tuple(supervisor.connections), return_exceptions=True)
        await server.wait_closed()
        supervisor.store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
