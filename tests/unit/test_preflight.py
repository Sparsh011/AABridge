"""Foundation checks only: no real agent inference or network connections."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.main import main
from aabridge_preflight.doctor import check_python, check_sqlite, collect_report
from aabridge_preflight.models import Check, Report
from aabridge_preflight.paths import check_state_directory, default_state_directory
from aabridge_preflight.probes import TOOLS, ProbeOutput, _run_probe, check_agent_interface, check_tool


class RuntimeTests(unittest.TestCase):
    def test_python_311_supported(self):
        with patch("aabridge_preflight.doctor.sys.version_info", (3, 11, 17)):
            self.assertEqual(check_python().status, "ok")

    def test_old_python_rejected(self):
        with patch("aabridge_preflight.doctor.sys.version_info", (3, 9, 6)):
            self.assertEqual(check_python().status, "error")

    def test_sqlite_transaction_rolls_back(self):
        self.assertEqual(check_sqlite().status, "ok")

    def test_missing_sqlite_is_reported(self):
        with patch("aabridge_preflight.doctor.sqlite3", None):
            self.assertEqual(check_sqlite().status, "error")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)

    def test_absent_directory_is_not_created(self):
        target = self.root / "missing" / "state"
        self.assertEqual(check_state_directory(target).status, "warning")
        self.assertFalse(target.parent.exists())

    def test_private_directory_and_file_accepted_without_reading(self):
        config = self.root / "config.toml"
        config.write_text("SECRET_DO_NOT_READ")
        config.chmod(0o600)
        with patch.object(Path, "read_text", side_effect=AssertionError("must not read")):
            result = check_state_directory(self.root)
        self.assertEqual(result.status, "ok")
        self.assertNotIn("SECRET", result.summary)

    def test_group_accessible_directory_rejected(self):
        self.root.chmod(0o750)
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_other_owner_rejected(self):
        with patch("aabridge_preflight.paths.os.getuid", return_value=os.getuid() + 1):
            self.assertEqual(check_state_directory(self.root).status, "error")

    def test_public_state_file_rejected(self):
        config = self.root / "config.toml"
        config.touch(mode=0o644)
        config.chmod(0o644)
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_state_directory_symlink_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(check_state_directory(alias).status, "error")

    def test_symlink_ancestor_even_when_leaf_missing_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(check_state_directory(alias / "missing").status, "error")

    def test_dangling_symlink_rejected(self):
        (self.root / "config.toml").symlink_to(self.root / "missing-secret")
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_state_file_symlink_rejected(self):
        secret = self.root / "secret"
        secret.write_text("not bridge state")
        secret.chmod(0o600)
        (self.root / "bridge.sqlite3").symlink_to(secret)
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_directory_instead_of_state_file_rejected(self):
        (self.root / "config.toml").mkdir(mode=0o700)
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_regular_file_instead_of_socket_rejected(self):
        (self.root / "bridge.sock").touch(mode=0o600)
        self.assertEqual(check_state_directory(self.root).status, "error")

    def test_file_instead_of_state_directory_rejected(self):
        path = self.root / "file"
        path.touch()
        self.assertEqual(check_state_directory(path).status, "error")

    def test_parent_traversal_rejected(self):
        self.assertEqual(check_state_directory(self.root / ".." / "state").status, "error")

    def test_relative_path_rejected(self):
        self.assertEqual(check_state_directory(Path("state")).status, "error")

    def test_path_inspection_failure_is_reported(self):
        with patch.object(Path, "lstat", side_effect=PermissionError):
            self.assertEqual(check_state_directory(self.root).status, "error")

    def test_default_mac_path(self):
        with patch("aabridge_preflight.paths.sys.platform", "darwin"), patch.object(Path, "home", return_value=self.root):
            self.assertEqual(default_state_directory(), self.root / "Library/Application Support/AABridge")


class ToolTests(unittest.TestCase):
    def test_missing_agent_is_warning(self):
        with patch("aabridge_preflight.probes.shutil.which", return_value=None), patch(
            "aabridge_preflight.runtimes.locate_runtime", side_effect=ValueError("fixture: unavailable")
        ):
            result, executable = check_tool(TOOLS[-1])
        self.assertEqual(result.status, "warning")
        self.assertIsNone(executable)

    def test_version_extracted_without_echoing_raw_output(self):
        with patch("aabridge_preflight.probes.shutil.which", return_value="/installed/codex"), patch(
            "aabridge_preflight.probes._run_probe",
            return_value=ProbeOutput("ok", "SECRET=value\ncodex-cli 0.163.0-alpha.11.2\n"),
        ):
            result, executable = check_tool(TOOLS[2])
        self.assertEqual(result.status, "ok")
        self.assertIn("0.163.0-alpha.11.2", result.summary)
        self.assertNotIn("SECRET", result.summary)
        self.assertEqual(executable, "/installed/codex")

    def test_unrecognized_output_is_not_echoed(self):
        with patch("aabridge_preflight.probes.shutil.which", return_value="/installed/claude"), patch(
            "aabridge_preflight.probes._run_probe", return_value=ProbeOutput("ok", "SECRET=value"),
        ):
            result, executable = check_tool(TOOLS[-1])
        self.assertEqual(result.status, "warning")
        self.assertNotIn("SECRET", result.summary)
        self.assertIsNone(executable)

    def test_version_failures_do_not_claim_compatibility(self):
        for status in ("failed", "timeout", "oversized"):
            with self.subTest(status=status), patch("aabridge_preflight.probes.shutil.which", return_value="/installed/git"), patch(
                "aabridge_preflight.probes._run_probe", return_value=ProbeOutput(status),
            ):
                result, executable = check_tool(TOOLS[0])
                self.assertEqual(result.status, "warning")
                self.assertIsNone(executable)

    def test_interface_flags_not_execution_proof(self):
        with patch("aabridge_preflight.probes._run_probe", return_value=ProbeOutput("ok", "--listen stdio://")):
            result = check_agent_interface("codex", "/installed/codex")
        self.assertEqual(result.status, "ok")
        self.assertIn("unverified", result.summary)

    def test_missing_interface_flags_warn(self):
        with patch("aabridge_preflight.probes._run_probe", return_value=ProbeOutput("ok", "old CLI")):
            self.assertEqual(check_agent_interface("claude", "/installed/claude").status, "warning")

    def test_unknown_agent_interface_rejected(self):
        with self.assertRaises(ValueError):
            check_agent_interface("peer-supplied-command", "/arbitrary")

    def test_real_local_process_output(self):
        self.assertEqual(_run_probe(sys.executable, ("-c", "print('version-probe')")), ProbeOutput("ok", "version-probe\n"))

    def test_nonzero_exit_is_failure_even_with_output(self):
        result = _run_probe(sys.executable, ("-c", "print('success'); raise SystemExit(1)"))
        self.assertEqual(result, ProbeOutput("failed"))

    def test_oversized_output_rejected(self):
        result = _run_probe(sys.executable, ("-c", "print('x' * 20000)"))
        self.assertEqual(result, ProbeOutput("oversized"))

    def test_hanging_version_probe_is_killed(self):
        started = time.monotonic()
        with patch("aabridge_preflight.probes.PROBE_TIMEOUT_SECONDS", 0.15):
            result = _run_probe(sys.executable, ("-c", "import time; time.sleep(30)"))
        self.assertEqual(result, ProbeOutput("timeout"))
        self.assertLess(time.monotonic() - started, 3)


class ReportAndCliTests(unittest.TestCase):
    def test_healthy_foundation_does_not_enable_workers(self):
        value = Report((Check("runtime", "ok", "available"),)).to_dict()
        self.assertTrue(value["foundation_ready"])
        self.assertFalse(value["worker_execution_enabled"])
        self.assertEqual(value["verification"]["real_two_device_agents"], "not_verified")

    def test_error_report_fails_foundation(self):
        self.assertFalse(Report((Check("state", "error", "unsafe"),)).to_dict()["foundation_ready"])

    def test_cli_json_and_exit_status(self):
        for status, expected in (("ok", 0), ("warning", 0), ("error", 1)):
            with self.subTest(status=status):
                stream = io.StringIO()
                with patch("aabridge_cli.main.collect_report", return_value=Report((Check("test", status, "test"),))), contextlib.redirect_stdout(stream):
                    actual = main(["doctor", "--json"])
                self.assertEqual(actual, expected)
                self.assertEqual(json.loads(stream.getvalue())["schema_version"], 1)

    def test_cli_passes_local_state_override(self):
        with patch("aabridge_cli.main.collect_report", return_value=Report(())) as collect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["doctor", "--state-dir", "/local/state"]), 0)
        collect.assert_called_once_with(state_dir=Path("/local/state"))

    def test_no_task_execution_command_exposed(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["run", "untrusted command"])
        self.assertEqual(raised.exception.code, 2)

    def test_collection_does_not_create_state_or_call_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / "absent"
            with patch("aabridge_preflight.doctor.TOOLS", ()):
                report = collect_report(state_dir=target)
            self.assertFalse(target.exists())
            self.assertFalse(report.to_dict()["worker_execution_enabled"])
            identifiers = [check.id for check in report.checks]
            self.assertEqual(len(identifiers), len(set(identifiers)))


if __name__ == "__main__":
    unittest.main()
