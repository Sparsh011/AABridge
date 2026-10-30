import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_runtimes', Path(__file__).resolve().parents[2] / 'scripts/select_build_runtimes.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class BuildRuntimeTests(unittest.TestCase):
    def report(self, agent, path):
        return ({'status': 'found', 'executable': '/native/' + agent} if path == '/existing app'
                else {'status': 'unavailable', 'message': 'Missing runtime'})

    def test_prompt_retries_and_skips_optional_agent(self):
        with patch.object(module, 'runtime_report', side_effect=self.report), patch('builtins.input', side_effect=['bad', '"/existing app"', '']), patch('builtins.print'):
            self.assertEqual(module.select({}, interactive=True), {'codex': '/existing app'})

    def test_saved_valid_paths_do_not_prompt(self):
        with patch.object(module, 'runtime_report', side_effect=self.report), patch('builtins.input') as prompt, patch('builtins.print'):
            self.assertEqual(module.select({'codex': '/existing app', 'claude': '/existing app'}, interactive=True), {'codex': '/existing app', 'claude': '/existing app'})
            prompt.assert_not_called()

    def test_noninteractive_never_waits(self):
        with patch.object(module, 'runtime_report', side_effect=self.report), patch('builtins.input') as prompt, patch('builtins.print'):
            self.assertEqual(module.select({}, interactive=False), {})
            prompt.assert_not_called()
