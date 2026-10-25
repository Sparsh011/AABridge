from __future__ import annotations

import hashlib
import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aabridge_core.config import write_private
from aabridge_protocol.messages import BridgeError

source = Path(__file__).resolve().parents[2] / "apps/installer/setup_worker.py"
spec = importlib.util.spec_from_file_location("aabridge_worker_installer", source)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
ENTRY = 'restrict,command="/fixed/aabridge rpc --state-dir /private/state --peer macbook" ssh-ed25519 AAAA dedicated-test-key\n'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)

    def test_diagnostics_does_not_install_grant_or_authenticate_and_missing_codex_is_visible(self):
        from aabridge_adapter_codex.isolation import PROBE_CHECKS
        evidence = {"passed": True, "checks": dict.fromkeys(PROBE_CHECKS, True), "failed_checks": [], "diagnostic": ""}
        for found in (True, False):
            with self.subTest(codex_found=found), contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(sys, "argv", ["setup_worker.py", "--bundle", str(self.root), "--diagnose"]))
                stack.enter_context(patch.object(sys, "path", sys.path.copy()))
                stack.enter_context(patch.object(installer, "checked_bundle", return_value=(self.root / "fixture.whl", self.root / "key.pub", {})))
                stack.enter_context(patch.object(installer, "runtime_settings", return_value={}))
                stack.enter_context(patch("aabridge_adapter_codex.isolation.isolation_report", return_value=evidence))
                stack.enter_context(patch("aabridge_preflight.runtimes.runtime_report", side_effect=lambda agent, _: {
                    "status": "found" if agent == "codex" and found else "unavailable",
                    "executable": "/fixture/codex" if agent == "codex" and found else None, "message": "Choose an existing local executable."}))
                forbidden = stack.enter_context(patch("aabridge_core.config.private_directory", side_effect=AssertionError("Diagnostics may not create installation/state directories")))
                run = stack.enter_context(patch.object(installer.subprocess, "run", side_effect=AssertionError("Diagnostics may not install or launch an agent")))
                output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(installer.main(), 0 if found else 1)
                self.assertIn("DIAGNOSTICS PASSED:" if found else "DIAGNOSTICS INCOMPLETE:", output.getvalue())
                forbidden.assert_not_called()
                run.assert_not_called()

    def test_upgrade_refuses_active_or_closing_task_before_stopping_receiver(self):
        from aabridge_core.config import initialize
        from aabridge_core.store import Store
        state = self.root / "state"
        initialize(state)
        store = Store(state)
        try:
            for status, pid in (("running", 123), ("queued", None), ("cancelled", 123)):
                store.db.execute("DELETE FROM tasks")
                store.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", ("task-fixture", "demo", "macbook", status,
                                 json.dumps({"status": status, "worker_pid": pid})))
                with patch("aabridge_cli.actions.send") as send, self.assertRaisesRegex(BridgeError, "still active or closing"):
                    installer.stop_idle_receiver(state)
                send.assert_not_called()
        finally:
            store.close()

    def test_upgrade_stops_idle_receiver_and_preserves_completed_task(self):
        from aabridge_core.config import initialize, write_private
        from aabridge_core.store import Store
        state = self.root / "state"
        initialize(state)
        store = Store(state)
        document = json.dumps({"status": "cancelled", "worker_pid": None})
        store.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", ("task-fixture", "demo", "macbook", "cancelled", document))
        write_private(state / "daemon.lock", b"")
        try:
            with patch("aabridge_cli.actions.send", return_value={"stopping": True}) as send:
                installer.stop_idle_receiver(state)
            self.assertEqual(send.call_args.args[1].op, "shutdown")
            self.assertEqual(store.db.execute("SELECT document FROM tasks").fetchone()[0], document)
        finally:
            store.close()

    def bundled_app(self, name="ChatGPT.app"):
        application = self.root / "Applications" / name
        native = application / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        launcher = application / "Contents/Resources/codex-cli/bin/codex"
        for path in (native, launcher):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture only; never executed")
            path.chmod(0o755)
        return native, launcher

    def test_chatgpt_native_and_launcher_are_one_runtime_without_path_alias(self):
        native, _ = self.bundled_app()
        with patch.object(installer.shutil, "which", return_value=None):
            self.assertEqual(installer.locate_codex(application_roots=(self.root / "Applications",)), native)

    def test_chatgpt_launcher_on_path_is_resolved_to_native_executable(self):
        native, launcher = self.bundled_app()
        with patch.object(installer.shutil, "which", return_value=str(launcher)):
            self.assertEqual(installer.locate_codex(), native)

    def test_distinct_app_installations_still_require_an_explicit_choice(self):
        self.bundled_app()
        self.bundled_app("Codex.app")
        with patch.object(installer.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "Multiple distinct"):
            installer.locate_codex(application_roots=(self.root / "Applications",))

    def test_missing_runtime_is_reported_as_discovery_not_account_failure(self):
        with patch.object(installer.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "Could not locate"):
            installer.locate_codex(application_roots=(self.root / "Applications",))

    def test_bundle_runtime_settings_are_independent_and_locally_editable(self):
        settings = self.root / "runtime-paths.json"
        value = {"version": 1, "codex": "/Volumes/An SSD/Downloads/ChatGPT", "claude": "~/.local/bin/claude"}
        settings.write_text(json.dumps(value))
        self.assertEqual(installer.runtime_settings(self.root), {key: value[key] for key in ("codex", "claude")})

    def test_bundle_runtime_settings_reject_unknown_fields_and_relative_paths(self):
        settings = self.root / "runtime-paths.json"
        for value in ({"version": 1, "shell": "anything"}, {"version": 1, "codex": "relative/app"}):
            settings.write_text(json.dumps(value))
            with self.assertRaises((BridgeError, ValueError)):
                installer.runtime_settings(self.root)

    def test_existing_authorized_keys_preserved_and_new_entry_is_idempotent(self):
        directory = self.home / ".ssh"
        directory.mkdir(mode=0o700)
        path = directory / "authorized_keys"
        original = b"ssh-ed25519 BBBB existing-user-key"
        write_private(path, original)
        self.assertTrue(installer.append_authorized_key(self.home, ENTRY))
        self.assertEqual(path.read_bytes(), original + b"\n" + ENTRY.encode())
        self.assertFalse(installer.append_authorized_key(self.home, ENTRY))
        self.assertEqual(path.read_bytes(), original + b"\n" + ENTRY.encode())

    def test_existing_unrestricted_entry_for_same_key_is_not_silently_replaced(self):
        directory = self.home / ".ssh"
        directory.mkdir(mode=0o700)
        path = directory / "authorized_keys"
        original = b"ssh-ed25519 AAAA existing-authority\n"
        write_private(path, original)
        with self.assertRaises(BridgeError):
            installer.append_authorized_key(self.home, ENTRY)
        self.assertEqual(path.read_bytes(), original)

    def test_symlinked_and_hardlinked_authorized_keys_are_rejected(self):
        directory = self.home / ".ssh"
        directory.mkdir(mode=0o700)
        path = directory / "authorized_keys"
        target = self.root / "canary"
        write_private(target, b"preserved")
        path.symlink_to(target)
        with self.assertRaises((OSError, BridgeError)):
            installer.append_authorized_key(self.home, ENTRY)
        path.unlink()
        os.link(target, path)
        with self.assertRaises(BridgeError):
            installer.append_authorized_key(self.home, ENTRY)
        self.assertEqual(target.read_bytes(), b"preserved")

    def test_unrestricted_or_multiline_entry_creates_no_ssh_directory(self):
        for entry in ("ssh-ed25519 AAAA bare-key\n", ENTRY + "ssh-ed25519 BBBB injected\n"):
            with self.assertRaises(BridgeError):
                installer.append_authorized_key(self.home, entry)
        self.assertFalse((self.home / ".ssh").exists())

    def test_bundle_tampering_detected_before_installation(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        wheel = bundle / "aabridge-0.2.0.dev1-py3-none-any.whl"
        wheel.write_bytes(b"fixture wheel content")
        public = bundle / "coordinator.pub"
        public.write_bytes(b"ssh-ed25519 AAAA public-only\n")
        manifest = {"version": 1, "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    "public_key_sha256": hashlib.sha256(public.read_bytes()).hexdigest(), "peer_id": "macbook"}
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(installer.checked_bundle(bundle)[0], wheel)
        wheel.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            installer.checked_bundle(bundle)

    def test_bundle_cannot_select_parent_wheel_path(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(json.dumps({"version": 1, "wheel": "../aabridge-example-py3-none-any.whl"}))
        with self.assertRaises(ValueError):
            installer.checked_bundle(bundle)
