from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import configure_runtimes, demo_init
from aabridge_cli.main import main
from aabridge_core.config import initialize, load_config, save_config
from aabridge_preflight.probes import TOOLS, check_tool
from aabridge_preflight.runtimes import runtime_report
from aabridge_protocol.messages import BridgeError


class RuntimeLocationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        initialize(self.state)
        self.app = self.root / "SSD with spaces" / "Downloads/ChatGPT.app"
        self.codex = self.app / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("fixture only; must never execute")
        self.codex.chmod(0o755)
        self.claude = self.root / "local/share/claude/versions/test-version"
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text("fixture only; must never execute")
        self.claude.chmod(0o755)
        self.local_bin = self.root / "local/bin"
        self.local_bin.mkdir()
        (self.local_bin / "claude").symlink_to(self.claude)

    def test_unplugged_selected_drive_does_not_fall_back_to_another_codex(self):
        missing = f"/Volumes/AABridge-Unmounted-{self.root.name}/Downloads/ChatGPT.app"
        with patch("aabridge_preflight.runtimes.shutil.which", return_value=str(self.codex)) as which:
            report = runtime_report("codex", missing)
        self.assertEqual(report["status"], "unavailable")
        self.assertIn("Reconnect", report["message"])
        which.assert_not_called()

    def test_codex_drive_loss_does_not_hide_independent_local_claude(self):
        with patch("aabridge_preflight.runtimes.shutil.which", return_value=None):
            codex = runtime_report("codex", str(self.root / "missing/ChatGPT.app"))
            claude = runtime_report("claude", command_roots=(self.local_bin,))
        self.assertEqual(codex["status"], "unavailable")
        self.assertEqual(claude["status"], "found")
        self.assertEqual(claude["executable"], str(self.claude))
        self.assertTrue(claude["adapter_available"])
        self.assertEqual(claude["authentication"], "not_checked")

    def test_settings_preserve_per_agent_paths_and_existing_project_approval(self):
        config = load_config(self.state)
        approval = {"codex_executable": "/previous/approved/codex", "codex_sha256": "original-hash"}
        config["projects"]["existing"] = approval.copy()
        save_config(self.state, config)
        report = configure_runtimes(self.state, {"codex": str(self.app), "claude": str(self.local_bin / "claude")})
        saved = load_config(self.state)
        self.assertEqual(saved["projects"]["existing"], approval)
        self.assertEqual(saved["runtimes"]["codex"]["path"], str(self.app))
        self.assertEqual(report["claude"]["executable"], str(self.claude))

    def test_new_project_pins_selected_native_runtime_without_executing_it(self):
        configure_runtimes(self.state, {"codex": str(self.app.with_suffix("")), "claude": str(self.claude)})
        demo_init(self.state, self.root / "source", "demo")
        project = load_config(self.state)["projects"]["demo"]
        self.assertEqual(project["codex_executable"], str(self.codex))
        self.assertEqual(project["codex_sha256"], hashlib.sha256(self.codex.read_bytes()).hexdigest())
        self.assertFalse(project["execution_enabled"])
        self.assertNotIn(str(self.app), project["codex_auth_file"])

    def test_cli_saves_offline_selection_and_reports_unavailable(self):
        output = io.StringIO()
        missing = str(self.root / "disconnected/ChatGPT.app")
        with contextlib.redirect_stdout(output):
            code = main(["runtime", "set", "--state-dir", str(self.state), "--agent", "codex", "--path", missing])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["codex"]["status"], "unavailable")
        self.assertEqual(load_config(self.state)["runtimes"]["codex"]["path"], missing)

    def test_unavailable_runtime_does_not_leave_an_unregistered_demo_directory(self):
        configure_runtimes(self.state, {"codex": str(self.root / "missing/ChatGPT.app")})
        target = self.root / "new-demo"
        with self.assertRaises(BridgeError):
            demo_init(self.state, target, "demo")
        self.assertFalse(target.exists())

    def test_configured_missing_runtime_diagnostic_does_not_probe_path_alternative(self):
        with patch("aabridge_preflight.probes.shutil.which", return_value=str(self.codex)), patch("aabridge_preflight.probes._run_probe") as probe:
            check, executable = check_tool(TOOLS[2], configured_path=str(self.root / "missing/ChatGPT.app"))
        self.assertEqual(check.status, "warning")
        self.assertIsNone(executable)
        probe.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "macOS sandbox profile")
    def test_external_app_read_grant_does_not_expand_to_whole_drive(self):
        from aabridge_adapter_codex.isolation import sandbox_profile
        executable = Path("/Volumes/Example SSD/Downloads/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
        profile = sandbox_profile(self.root / "runtime", executable)
        self.assertIn('(subpath "/Volumes/Example SSD/Downloads/ChatGPT.app")', profile)
        self.assertNotIn('(subpath "/Volumes/Example SSD")', profile)
        self.assertNotIn('(subpath "/Volumes")', profile)
