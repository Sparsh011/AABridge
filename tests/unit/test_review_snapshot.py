from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from aabridge_cli.actions import request
from aabridge_core.config import initialize, load_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError, review_snapshot
from aabridge_workspace.git import Workspace, git
from aabridge_workspace.review import export_snapshot, import_snapshot


class ReviewSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-review-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        git(self.source, 'init', '--initial-branch=main', '--template=')
        (self.source / 'code.txt').write_text('baseline\n')
        git(self.source, 'add', '--all'); git(self.source, 'commit', '-m', 'Base')
        self.base = git(self.source, 'rev-parse', 'HEAD')
        self.review = self.root / 'review'
        git(self.root, 'clone', '--no-local', '--template=', str(self.source), str(self.review))
        self.change('implementation')

    def change(self, content):
        (self.source / 'code.txt').write_text(content)
        git(self.source, 'add', '--all'); git(self.source, 'commit', '-m', content)
        self.commit = git(self.source, 'rev-parse', 'HEAD')

    def test_exact_git_commit_and_files_transfer_without_source_path_or_remote(self):
        snapshot = export_snapshot(self.source, self.base, self.commit)
        self.assertNotIn(str(self.source), str(snapshot))
        # The receiver starts with just the old baseline and cannot resolve the new SHA.
        with self.assertRaises(BridgeError):
            git(self.review, 'cat-file', '-e', self.commit)
        import_snapshot(self.review, self.base, snapshot, 'codex/review')
        self.assertEqual(git(self.review, 'rev-parse', 'HEAD'), self.commit)
        self.assertEqual((self.review / 'code.txt').read_text(), 'implementation')
        self.assertIn('+implementation', git(self.review, 'diff', self.base, self.commit))
        self.assertFalse(git(self.source, 'for-each-ref', 'refs/aabridge-review/'))

    def test_second_round_moves_the_same_review_branch_to_the_new_exact_commit(self):
        import_snapshot(self.review, self.base, export_snapshot(self.source, self.base, self.commit), 'codex/review')
        self.change('fixed implementation')
        import_snapshot(self.review, self.base, export_snapshot(self.source, self.base, self.commit), 'codex/review')
        self.assertEqual(git(self.review, 'branch', '--show-current'), 'codex/review')
        self.assertEqual(git(self.review, 'rev-parse', 'HEAD'), self.commit)
        self.assertEqual((self.review / 'code.txt').read_text(), 'fixed implementation')

    def test_wrong_commit_or_invalid_bundle_never_changes_review_head(self):
        snapshot = export_snapshot(self.source, self.base, self.commit)
        for invalid in ({**snapshot, 'commit': 'a' * 40}, {**snapshot, 'bundle': base64.b64encode(b'not a bundle').decode()}):
            with self.assertRaises(BridgeError):
                import_snapshot(self.review, self.base, invalid, 'codex/review')
            self.assertEqual(git(self.review, 'rev-parse', 'HEAD'), self.base)

    def test_dirty_review_is_preserved(self):
        (self.review / 'code.txt').write_text('keep my edits')
        with self.assertRaises(BridgeError):
            import_snapshot(self.review, self.base, export_snapshot(self.source, self.base, self.commit), 'codex/review')
        self.assertEqual((self.review / 'code.txt').read_text(), 'keep my edits')

    def test_bundle_bounds_and_encoding_are_checked(self):
        for bundle in ('!', 'x' * 80001, base64.b64encode(b'wrong format').decode()):
            with self.assertRaises(BridgeError):
                review_snapshot({'commit': self.commit, 'bundle': bundle})

    def test_workspace_reviews_the_implementation_and_checks_it_stays_unchanged(self):
        state = self.root / 'state'; initialize(state)
        task = {'id': 'task-review', 'base_commit': self.base, 'mode': 'review',
                'review_snapshot': export_snapshot(self.source, self.base, self.commit)}
        # The locally approved project still has the old baseline.
        project = {'repository': str(self.review), 'adapter': 'codex', 'verification': {'profile': 'repository-v1'}}
        workspace = Workspace(state, task, project)
        workspace.prepare()
        self.assertEqual(git(workspace.path, 'rev-parse', 'HEAD'), self.commit)
        result = workspace.coding_result({'summary': 'Reviewed actual implementation'})
        self.assertEqual(result['result_commit'], self.commit)
        self.assertEqual(result['base_commit'], self.base)
        (workspace.path / 'code.txt').write_text('unauthorized review edit')
        with self.assertRaises(BridgeError):
            workspace.coding_result({'summary': 'Must reject edits'})

    def test_receiver_persists_new_review_snapshot_on_revision_and_rejects_work_injection(self):
        state = self.root / 'receiver'; initialize(state)
        config = load_config(state)
        limits = {'runtime_seconds': 60, 'max_turns': 10, 'max_revisions': 5}
        project = {'worker': 'codex', 'adapter': 'codex', 'base_commit': self.base, 'scope': ['*'],
                   'permission_profile': 'coding-v1', 'limits': limits, 'execution_enabled': True}
        config['projects']['app'] = project
        snapshot = export_snapshot(self.source, self.base, self.commit)
        body = {'project_id': 'app', 'recipient': 'codex', 'base_commit': self.base, 'objective': 'Review',
                'scope': ['*'], 'acceptance': ['Review exact files'], 'context': [], 'limits': limits,
                'permission_profile': 'coding-v1', 'mode': 'review', 'review_snapshot': snapshot}
        store = Store(state); self.addCleanup(store.close)
        review = store.handle(request('submit', body, 'submit-review'), 'local', config)
        store.update(review['id'], 'result', status='completed', result={})
        self.change('next implementation')
        updated = export_snapshot(self.source, self.base, self.commit)
        revised = store.handle(request('revise', {'task_id': review['id'], 'revision': 1,
                               'text': 'Review next revision', 'review_snapshot': updated}, 'revise-review'), 'local', config)
        self.assertEqual(revised['review_snapshot']['commit'], self.commit)
        self.assertEqual(store.get(review['id'])['review_snapshot'], updated)
        work_body = {**body, 'mode': 'work'}
        work_body.pop('review_snapshot')
        work = store.handle(request('submit', work_body, 'submit-work'), 'local', config)
        store.update(work['id'], 'result', status='completed', result={})
        with self.assertRaises(BridgeError):
            store.handle(request('revise', {'task_id': work['id'], 'revision': 1,
                         'text': 'Invalid injection', 'review_snapshot': snapshot}, 'inject-snapshot'), 'local', config)
        self.assertNotIn('review_snapshot', store.get(work['id']))
        for mode in ('work', 'plan'):
            with self.assertRaises(BridgeError):
                request('submit', {**body, 'mode': mode}, 'invalid-role')
