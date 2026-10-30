from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_fixture import select_fake_codex
from aabridge_cli.actions import demo_init, request
from aabridge_core.config import authenticate, initialize, load_config, read_private, save_config, write_private
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError, decode, validate_request
from aabridge_workspace.git import Workspace, git, verify_source


class EngineFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        initialize(self.state)
        select_fake_codex(self.state)
        demo_init(self.state, self.root / "repo", "demo")
        self.config = load_config(self.state)
        self.project = self.config["projects"]["demo"]
        self.store = Store(self.state)
        self.addCleanup(self.store.close)
        self.body = {"project_id": "demo", "recipient": "codex", "base_commit": self.project["base_commit"],
                     "objective": "Fix add", "scope": ["math_ops.py"], "acceptance": ["Addition passes"],
                     "context": [], "limits": self.project["limits"], "permission_profile": "proposal-v1"}

    def submit(self, key="request-one", identity="local"):
        return self.store.handle(request("submit", self.body, key), identity, self.config)

    def test_submission_survives_database_reopen(self):
        task = self.submit()
        other = Store(self.state)
        try:
            self.assertEqual(other.get(task["id"])["base_commit"], self.body["base_commit"])
        finally:
            other.close()

    def test_newer_database_version_is_not_overwritten(self):
        self.store.db.execute("PRAGMA user_version=99")
        try:
            with self.assertRaises(BridgeError) as raised:
                Store(self.state)
            self.assertEqual(raised.exception.code, "unsupported_version")
            self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 99)
        finally:
            self.store.db.execute("PRAGMA user_version=1")

    def test_symlinked_sqlite_journal_is_rejected_before_open(self):
        target = self.root / "canary"
        target.write_text("unchanged")
        journal = self.state / "bridge.sqlite3-journal"
        journal.symlink_to(target)
        try:
            with self.assertRaises((OSError, BridgeError)):
                Store(self.state)
            self.assertEqual(target.read_text(), "unchanged")
        finally:
            journal.unlink()

    def test_arithmetic_profile_rejects_duplicate_parameters(self):
        with self.assertRaises(BridgeError):
            verify_source("def add(a, a):\n    return a + a\n", self.project["verification"])

    def test_duplicate_mutation_returns_same_task(self):
        first, second = self.submit(), self.submit()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_changed_content_replay_rejected(self):
        self.submit()
        self.body["objective"] = "Different task"
        with self.assertRaises(BridgeError) as raised:
            self.submit()
        self.assertEqual(raised.exception.code, "replay_conflict")

    def test_unknown_project_and_scope_rejected(self):
        for field, value in (("project_id", "unassigned"), ("scope", ["other.py"]), ("recipient", "peer-command"), ("base_commit", "0" * 40)):
            with self.subTest(field=field):
                original = self.body[field]
                self.body[field] = value
                with self.assertRaises(BridgeError):
                    self.submit()
                self.body[field] = original

    def test_permission_limits_cannot_expand(self):
        self.body["limits"] = {**self.body["limits"], "runtime_seconds": 300}
        with self.assertRaises(BridgeError):
            self.submit()

    def test_cross_peer_task_access_denied(self):
        self.config["peers"]["alice"] = {"enabled": True, "projects": ["demo"], "digest": "unused"}
        self.config["peers"]["bob"] = {"enabled": True, "projects": ["demo"], "digest": "unused"}
        task = self.submit(identity="alice")
        with self.assertRaises(BridgeError):
            self.store.handle(request("status", {"task_id": task["id"]}), "bob", self.config)

    def test_revocation_applies_to_cached_mutation(self):
        self.submit()
        self.config["peers"]["local"]["enabled"] = False
        with self.assertRaises(BridgeError):
            self.submit()

    def test_authentication_requires_private_credential_not_task_id(self):
        task = self.submit()
        with self.assertRaises(BridgeError):
            authenticate(self.config, task["id"])
        self.assertEqual(authenticate(self.config, read_private(self.state / "local.token").decode()), "local")

    def test_stale_and_cross_task_answers_rejected(self):
        task = self.submit()
        self.store.update(task["id"], "question", status="waiting_question", question={"id": "question-one", "text": "Which operation?"}, thread_id="exact-thread")
        for question, revision in (("question-wrong", 1), ("question-one", 2)):
            with self.subTest(question=question, revision=revision), self.assertRaises(BridgeError):
                self.store.handle(request("answer", {"task_id": task["id"], "question_id": question, "revision": revision, "text": "Addition"}), "local", self.config)
        answered = self.store.handle(request("answer", {"task_id": task["id"], "question_id": "question-one", "revision": 1, "text": "Addition"}), "local", self.config)
        self.assertEqual(answered["status"], "queued")
        self.assertEqual(answered["thread_id"], "exact-thread")
        self.assertIsNone(answered["question"])

    def test_revision_requires_completed_result_and_enforces_round_limit(self):
        task = self.submit()
        body = {"task_id": task["id"], "revision": 1, "text": "Add a docstring"}
        with self.assertRaises(BridgeError):
            self.store.handle(request("revise", body), "local", self.config)
        self.store.update(task["id"], "result", status="completed", result={"summary": "done"}, thread_id="exact-thread")
        revised = self.store.handle(request("revise", body), "local", self.config)
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["thread_id"], "exact-thread")
        self.assertNotEqual(revised["attempt_id"], task["attempt_id"])
        self.store.update(task["id"], "result", status="completed")
        with self.assertRaises(BridgeError):
            self.store.handle(request("revise", {**body, "revision": 2}), "local", self.config)

    def test_events_have_ordered_resumable_cursors(self):
        task = self.submit()
        self.store.update(task["id"], "question", status="waiting_question", question={"id": "question-fixture", "text": "Should add perform addition?"})
        first = self.store.handle(request("events", {"task_id": task["id"], "cursor": 0}), "local", self.config)
        self.assertEqual([event["kind"] for event in first["events"]], ["submitted", "question"])
        second = self.store.handle(request("events", {"task_id": task["id"], "cursor": first["cursor"]}), "local", self.config)
        self.assertEqual(second["events"], [])

    def test_restart_does_not_restart_uncertain_workers(self):
        task = self.submit()
        self.store.update(task["id"], "running", status="running", worker_pid=123)
        self.store.recover()
        self.assertEqual(self.store.get(task["id"])["status"], "interrupted")
        self.assertTrue(self.store.project_interrupted("demo"))
        self.assertEqual(self.store.queued(), [])

    def test_queued_cancellation(self):
        task = self.submit()
        result = self.store.handle(request("cancel", {"task_id": task["id"]}), "local", self.config)
        self.assertEqual(result["status"], "cancelled")

    def test_workspace_result_is_reviewable_and_active_checkout_untouched(self):
        task = self.submit()
        workspace = Workspace(self.state, task, self.project)
        original = (self.root / "repo/math_ops.py").read_text()
        workspace.prepare()
        result = workspace.result({"edits": [{"path": "math_ops.py", "content": "def add(a, b):\n    return a + b\n"}], "summary": "Fixed addition", "issues": []})
        self.assertEqual(result["changed_paths"], ["math_ops.py"])
        self.assertNotEqual(result["base_commit"], result["result_commit"])
        self.assertEqual(result["verification"][0]["exit_code"], 0)
        self.assertEqual((self.root / "repo/math_ops.py").read_text(), original)
        self.assertEqual(git(self.root / "repo", "rev-parse", "HEAD"), self.project["base_commit"])

    def test_out_of_scope_and_deleting_proposals_rejected(self):
        workspace = Workspace(self.state, self.submit(), self.project)
        workspace.prepare()
        for edit in ({"path": "other.py", "content": "x"}, {"path": "math_ops.py", "content": ""}, {"path": "../outside", "content": "x"}):
            with self.subTest(edit=edit), self.assertRaises(BridgeError):
                workspace.result({"edits": [edit], "summary": "bad", "issues": []})

    def test_verification_does_not_execute_source(self):
        marker = self.root / "must-not-exist"
        attacks = [f"import pathlib\npathlib.Path({str(marker)!r}).touch()\ndef add(a,b): return a+b\n",
                   "def add(a,b): return __import__('os').getuid()\n",
                   "def add(a,b):\n    while True: pass\n",
                   "@print('should not run')\ndef add(a,b): return a+b\n"]
        for source in attacks:
            with self.subTest(source=source), self.assertRaises(BridgeError):
                verify_source(source, self.project["verification"])
        self.assertFalse(marker.exists())

    def test_committed_symlink_is_not_checked_out(self):
        (self.root / "repo/link").symlink_to(self.root / "outside")
        git(self.root / "repo", "add", "link")
        git(self.root / "repo", "commit", "-m", "Test malicious symlink")
        self.project["base_commit"] = self.body["base_commit"] = git(self.root / "repo", "rev-parse", "HEAD")
        task = self.submit()
        with self.assertRaises(BridgeError):
            Workspace(self.state, task, self.project).prepare()

    def test_hardlinked_state_is_not_truncated(self):
        original = self.root / "original"
        original.write_text("preserve me")
        original.chmod(0o600)
        alias = self.state / "alias"
        os.link(original, alias)
        with self.assertRaises(BridgeError):
            write_private(alias, b"overwrite")
        self.assertEqual(original.read_text(), "preserve me")


class ProtocolTests(unittest.TestCase):
    def test_malformed_and_duplicate_json_rejected(self):
        for value in (b"{", b'{"x":1,"x":2}', b'{"x":NaN}', b'x' * 131073):
            with self.subTest(value=value[:30]), self.assertRaises(BridgeError):
                decode(value)

    def test_commands_and_sender_impersonation_are_not_protocol_operations(self):
        for value in ({"version": 1, "op": "execute", "key": "key-one", "body": {"command": "sudo anything"}},
                      {"version": 1, "op": "ping", "key": "key-one", "body": {}, "sender": "human"}):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                validate_request(value)

    def test_boolean_is_not_a_valid_revision(self):
        with self.assertRaises(BridgeError):
            request("revise", {"task_id": "task-one", "revision": True, "text": "change"})

    def test_remote_shutdown_is_denied(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as name:
            state = Path(name).resolve() / "state"
            initialize(state)
            store = Store(state)
            try:
                with self.assertRaises(BridgeError):
                    store.handle(request("shutdown", {}), "peer", load_config(state))
            finally:
                store.close()
