from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_protocol.messages import BridgeError

spec = importlib.util.spec_from_file_location("activity_backend", Path(__file__).resolve().parents[2] / "apps/desktop/activity_backend.py")
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class ActivityBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)
        data = b"fixture unused wheel; bridge imports are already loaded"
        (self.bundle / "fixture.whl").write_bytes(data)
        (self.bundle / "manifest.json").write_text(json.dumps({"wheel": "fixture.whl", "wheel_sha256": hashlib.sha256(data).hexdigest()}))

    def test_exactly_full_last_event_page_preserves_all_events(self):
        first = {"events": [{"cursor": i + 1, "kind": "fixture"} for i in range(64)], "cursor": 64}
        args = ["activity_backend.py", "--bundle", str(self.bundle), "--action", "events", "--task-id", "task-fixture", "--all"]
        with patch.object(sys, "argv", args), patch.object(sys, "path", sys.path.copy()), patch("aabridge_cli.actions.send", side_effect=[first, {"events": [], "cursor": 64}]), contextlib.redirect_stdout(io.StringIO()) as output:
            backend.main()
        self.assertEqual(len(json.loads(output.getvalue())["events"]), 64)

    def test_old_receiver_requests_upgrade_instead_of_showing_empty_history(self):
        args = ["activity_backend.py", "--bundle", str(self.bundle), "--action", "tasks"]
        with patch.object(sys, "argv", args), patch.object(sys, "path", sys.path.copy()), patch("aabridge_cli.actions.send", side_effect=BridgeError("invalid_request", "Unsupported operation.")):
            with self.assertRaisesRegex(ValueError, "needs the conversation-history/chat-controls update"):
                backend.main()

    def test_gui_actions_keep_retry_key_and_record_human_origin(self):
        submit = {"project_id": "demo", "recipient": "codex", "base_commit": "a" * 40, "objective": "Fix add",
                  "scope": ["math_ops.py"], "acceptance": ["Return the sum"], "context": [],
                  "limits": {"runtime_seconds": 180, "max_turns": 4, "max_revisions": 1}, "permission_profile": "proposal-v1"}
        for action, body in (("submit", submit), ("answer", {"task_id": "task-one", "revision": 1, "question_id": "question-one", "text": "Yes, addition"}),
                             ("revise", {"task_id": "task-one", "revision": 1, "text": "Add a docstring"}), ("cancel", {"task_id": "task-one"})):
            if action != "cancel":
                body = {**body, "origin": {"kind": "agent", "name": "Spoofed GUI agent"}}
            data = json.dumps({"key": "gui-retry-same-key", "body": body}).encode()
            with self.subTest(action=action), contextlib.ExitStack() as stack:
                stdin = io.TextIOWrapper(io.BytesIO(data))
                stack.callback(stdin.close)
                stack.enter_context(patch.object(sys, "argv", ["activity_backend.py", "--bundle", str(self.bundle), "--action", action]))
                stack.enter_context(patch.object(sys, "path", sys.path.copy()))
                stack.enter_context(patch.object(sys, "stdin", stdin))
                send = stack.enter_context(patch("aabridge_cli.actions.send", return_value={"id": "task-one"}))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                backend.main()
                command = send.call_args.args[1]
                self.assertEqual(command.key, "gui-retry-same-key")
                self.assertEqual(command.op, action)
                if action != "cancel":
                    self.assertEqual(command.body["origin"], {"kind": "human", "name": "You"})
                else:
                    self.assertEqual(command.body, {"task_id": "task-one"})

    def test_remote_project_configuration_is_not_exposed(self):
        for action in ("projects-local", "project-save"):
            args = ["activity_backend.py", "--bundle", str(self.bundle), "--action", action, "--remote", "mini"]
            with patch.object(sys, "argv", args), patch.object(sys, "path", sys.path.copy()), patch("aabridge_cli.actions.send") as send:
                with self.assertRaisesRegex(BridgeError, "locally on the receiving Mac"):
                    backend.main()
                send.assert_not_called()
