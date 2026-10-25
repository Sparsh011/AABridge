from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import configure_file_project, request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError
from aabridge_workspace.git import Workspace, git, validate_verifier, verify_source


class FileProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-files-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state, self.repo = self.root / "state", self.root / "repo"
        initialize(self.state)
        self.repo.mkdir()
        (self.repo / "README.md").write_text("Original\n")
        git(self.repo, "init", "--initial-branch=main", "--template=")
        git(self.repo, "add", "--", "README.md")
        git(self.repo, "commit", "-m", "Fixture baseline")
        config = load_config(self.state)
        config["peers"]["macbook"] = {"enabled": True, "projects": [], "digest": "not-a-credential"}
        save_config(self.state, config)
        self.runtime = patch("aabridge_cli.actions.runtime_inventory", return_value={"codex": {"status": "found", "executable": sys.executable}})
        self.runtime.start(); self.addCleanup(self.runtime.stop)

    def register(self, scope=None, peers=None):
        return configure_file_project(self.state, "backend", self.repo, scope or ["README.md", "notes/hello.txt"], ["macbook"] if peers is None else peers)

    def task(self, scope):
        config = load_config(self.state)
        project = config["projects"]["backend"]
        store = Store(self.state); self.addCleanup(store.close)
        body = {"project_id": "backend", "recipient": "codex", "base_commit": project["base_commit"],
                "objective": "Create the approved file", "scope": scope, "acceptance": ["Human review required"],
                "context": [], "limits": project["limits"], "permission_profile": "proposal-v1"}
        return store, store.handle(request("submit", body), "macbook", config), project

    def test_new_empty_file_and_review_result_without_touching_original(self):
        self.register()
        store, task, project = self.task(["notes/hello.txt"])
        workspace = Workspace(self.state, task, project)
        self.assertEqual(workspace.prepare(), {"notes/hello.txt": None})
        result = workspace.result({"edits": [{"path": "notes/hello.txt", "content": ""}], "summary": "Empty file created", "issues": []})
        self.assertTrue((workspace.path / "notes/hello.txt").is_file())
        self.assertFalse((self.repo / "notes").exists())
        self.assertEqual(result["changed_paths"], ["notes/hello.txt"])
        self.assertTrue(result["verification"][0]["review_required"])
        store.update(task["id"], "result", status="completed", result=result)
        self.assertEqual(store.history_page(task["id"], 0)["messages"][-1]["kind"], "review_required_result")

    def test_arbitrary_text_is_data_never_executed(self):
        self.register(["README.md"])
        _, task, project = self.task(["README.md"])
        workspace = Workspace(self.state, task, project); workspace.prepare()
        canary = self.root / "must-not-exist"
        source = f"import pathlib\npathlib.Path({str(canary)!r}).touch()\n"
        result = workspace.result({"edits": [{"path": "README.md", "content": source}], "summary": "Text proposal", "issues": []})
        self.assertFalse(canary.exists())
        self.assertEqual((self.repo / "README.md").read_text(), "Original\n")
        self.assertTrue(result["verification"][0]["review_required"])

    def test_scope_cannot_expand_and_symlink_new_parent_is_rejected(self):
        self.register()
        _, task, project = self.task(["notes/hello.txt"])
        workspace = Workspace(self.state, task, project); workspace.prepare()
        with self.assertRaises(BridgeError):
            workspace.result({"edits": [{"path": "README.md", "content": "outside task"}], "summary": "No", "issues": []})
        (workspace.path / "notes").symlink_to(self.repo)
        with self.assertRaises(BridgeError):
            workspace.result({"edits": [{"path": "notes/hello.txt", "content": "escape"}], "summary": "No", "issues": []})
        self.assertFalse((self.repo / "hello.txt").exists())

    def test_project_scope_edit_blocks_active_tasks_and_preserves_other_grants(self):
        self.register()
        store, task, _ = self.task(["README.md"])
        with self.assertRaisesRegex(BridgeError, "active tasks"):
            self.register(["README.md"])
        store.update(task["id"], "cancelled", status="cancelled")
        self.register(["README.md"], peers=[])
        config = load_config(self.state)
        self.assertFalse(config["projects"]["backend"]["execution_enabled"])
        self.assertNotIn("backend", config["peers"]["macbook"]["projects"])

    def test_untracked_existing_file_and_revoked_peer_are_rejected_without_save(self):
        (self.repo / "untracked.txt").write_text("not committed")
        for scope, peers in [(["untracked.txt"], ["macbook"]), (["README.md"], ["unknown"])]:
            with self.subTest(scope=scope, peers=peers), self.assertRaises(BridgeError):
                configure_file_project(self.state, "backend", self.repo, scope, peers)
        self.assertNotIn("backend", load_config(self.state)["projects"])

    def test_malformed_scopes_and_byte_limit(self):
        for scope in ([], ["../outside"], ["/tmp/outside"], [".env"], ["a", "a"], [False]):
            with self.subTest(scope=scope), self.assertRaises(BridgeError):
                validate_verifier({"profile": "file-proposal-v1", "files": scope})
        self.register(["README.md"])
        _, task, project = self.task(["README.md"])
        workspace = Workspace(self.state, task, project); workspace.prepare()
        with self.assertRaisesRegex(BridgeError, "16 KiB"):
            workspace.result({"edits": [{"path": "README.md", "content": "界" * 6000}], "summary": "No", "issues": []})
        self.assertEqual((workspace.path / "README.md").read_text(), "Original\n")

    def test_demo_error_includes_inputs_actual_expected_and_no_commit(self):
        profile = {"profile": "integer-function-v1", "file": "math_ops.py", "function": "add", "cases": [{"args": [2, 3], "expected": 5}]}
        with self.assertRaisesRegex(BridgeError, r"add\(2, 3\) returned -1; expected 5"):
            verify_source("def add(a, b):\n    return a - b\n", profile)
