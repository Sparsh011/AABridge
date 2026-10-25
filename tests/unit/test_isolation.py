from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_adapter_codex.isolation import PROBE_CHECKS, isolation_report, probe_isolation, sandbox_profile
from aabridge_protocol.messages import BridgeError


class IsolationReportTests(unittest.TestCase):
    def report(self, checks, *, stderr=b"", code=0):
        result = subprocess.CompletedProcess([], code, json.dumps(checks).encode(), stderr)
        with patch("aabridge_adapter_codex.isolation.subprocess.run", return_value=result):
            return isolation_report()

    def test_every_canary_is_required_and_boolean(self):
        complete = dict.fromkeys(PROBE_CHECKS, True)
        self.assertTrue(self.report(complete)["passed"])
        for malformed in ({}, [], {next(iter(PROBE_CHECKS)): True}, {**complete, "unexpected": True},
                          {**complete, "child_exec_denied": 1}, {**complete, "network_denied": False}):
            with self.subTest(checks=malformed):
                self.assertFalse(self.report(malformed)["passed"])

    def test_interpreter_failure_explains_category_without_echoing_stderr(self):
        report = self.report({}, stderr=b"Fatal Python error: encodings missing; PRIVATE-CANARY", code=1)
        self.assertFalse(report["passed"])
        self.assertIn("standard library", report["diagnostic"])
        self.assertNotIn("PRIVATE-CANARY", json.dumps(report))
        self.assertEqual(report["probe_exit_code"], 1)

    def test_execution_gate_stays_closed_with_actionable_failure(self):
        report = self.report(dict.fromkeys(PROBE_CHECKS - {"child_exec_denied"}, True))
        with patch("aabridge_adapter_codex.isolation.isolation_report", return_value=report):
            with self.assertRaisesRegex(BridgeError, "child_exec_denied"):
                probe_isolation()

    def test_probe_runtime_grant_is_not_in_provider_profile(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as folder:
            root = Path(folder).resolve()
            prefix = root / "alternate-python-install"
            prefix.mkdir()
            executable = Path("/Applications/Codex.app/Contents/MacOS/codex")
            provider = sandbox_profile(root / "runtime", executable)
            canary = sandbox_profile(root / "runtime", executable, probe_python_root=prefix)
            self.assertNotIn(str(prefix), provider)
            self.assertIn(f'(subpath "{prefix}")', canary)
            self.assertIn("(deny process-fork)", canary)
            self.assertIn("(deny network-outbound", canary)
            with self.assertRaises(BridgeError):
                sandbox_profile(root / "runtime", executable, probe_python_root=Path("/"))
