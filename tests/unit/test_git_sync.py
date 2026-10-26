from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('git_sync', Path(__file__).resolve().parents[2] / 'scripts/git_sync.py')
sync = importlib.util.module_from_spec(spec); spec.loader.exec_module(sync)


class GitCooperationTests(unittest.TestCase):
    def test_guard_detects_dirty_main_and_missing_claude_commits(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-sync-') as tmp:
            repo = Path(tmp) / 'repo'; repo.mkdir()
            def git(*args):
                result = subprocess.run(['git','-C',str(repo),'-c','core.hooksPath=/dev/null','-c','user.name=Fixture','-c','user.email=fixture@localhost',*args], capture_output=True, text=True, check=True)
                return result.stdout.strip()
            old = sync.REPOSITORY; sync.REPOSITORY = repo
            try:
                git('init','--initial-branch=main')
                (repo / 'a.txt').write_text('base'); git('add','a.txt'); git('commit','-m','base')
                base = git('rev-parse','HEAD'); git('update-ref','refs/remotes/origin/main',base)
                with self.assertRaises(RuntimeError): sync.inspect(offline=True)
                git('switch','-c','codex/task')
                self.assertEqual(sync.inspect(offline=True)['head'], base)
                (repo / 'a.txt').write_text('codex')
                with self.assertRaises(RuntimeError): sync.inspect(offline=True)
                git('add','a.txt'); git('commit','-m','Codex feature')
                feature = git('rev-parse','HEAD')
                git('switch','main'); (repo / 'claude.txt').write_text('Claude work'); git('add','claude.txt'); git('commit','-m','Claude feature')
                other = git('rev-parse','HEAD'); git('update-ref','refs/remotes/origin/main',other)
                git('switch','codex/task')
                with self.assertRaisesRegex(RuntimeError, 'latest origin/main'): sync.inspect(offline=True)
                self.assertEqual(git('rev-parse','HEAD'), feature)
                git('merge','--no-edit','main')
                git('update-ref','refs/remotes/origin/codex/task',other)
                self.assertEqual(sync.inspect(offline=True)['branch'],'codex/task')
                # A divergent remote feature branch must also be integrated.
                git('switch','-c','other-worker',base); (repo / 'another.txt').write_text('other'); git('add','another.txt'); git('commit','-m','Other worker')
                git('update-ref','refs/remotes/origin/codex/task',git('rev-parse','HEAD')); git('switch','codex/task')
                with self.assertRaisesRegex(RuntimeError, 'GitHub has commits missing'): sync.inspect(offline=True)
            finally:
                sync.REPOSITORY = old
