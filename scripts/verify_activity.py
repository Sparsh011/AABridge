"""Verify the native viewer over real local IPC without launching a model.

The disposable fixture copies the captured first physical test's three messages;
it is explicitly labelled as verification data and is not a new agent run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from aabridge_cli.actions import demo_init, request, send, start_daemon
from aabridge_core.config import initialize, load_config
from aabridge_core.store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root / "docs/evidence/physical-mini-first-attempt-2026-10-25.json").read_text())
    temporary = Path(tempfile.mkdtemp(prefix="aab-viewer-", dir="/private/tmp")).resolve()
    state = temporary / "state"
    initialize(state)
    demo_init(state, temporary / "source", "demo")
    config = load_config(state)
    project = config["projects"]["demo"]
    body = {key: evidence["task"][key] for key in ("objective", "scope", "acceptance", "context", "permission_profile")}
    body.update(project_id="demo", recipient="codex", base_commit=project["base_commit"], limits=project["limits"])
    store = Store(state)
    try:
        task = store.handle(request("submit", body), "local", config)
        store.record_message(task["id"], "system", "verification_notice", "Viewer verification copy of the captured first MacBook–mini exchange. This fixture does not launch an agent or prove a new remote test.")
        store.update(task["id"], "question", status="waiting_question", question={"id": "question-verification", "text": evidence["question"]}, session_id=evidence["task"]["session_id"], thread_id=evidence["task"]["thread_id"])
        store.handle(request("answer", {"task_id": task["id"], "revision": 1, "question_id": "question-verification", "text": evidence["task"]["pending"]["text"]}), "local", config)
        store.handle(request("cancel", {"task_id": task["id"]}), "local", config)
    finally:
        store.close()
    start_daemon(state)
    try:
        process = subprocess.run([str(args.app.resolve() / "Contents/MacOS/AABridge"), "--verify-activity", str(state), str(args.preview.resolve())],
                                 check=True, text=True, capture_output=True, timeout=60)
        print(process.stdout)
    finally:
        send(state, request("shutdown", {}))
    print(json.dumps({"scope": "Native viewer, bundled Python wheel, actual IPC, verification fixture copied from captured physical-test messages", "state": str(state), "preview": str(args.preview.resolve()), "model_inference_started": False}, indent=2))


if __name__ == "__main__":
    main()
