from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import request
from aabridge_core.config import initialize, load_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError
from aabridge_workspace.git import Workspace, git


class WorkDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-work-delete-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'; initialize(self.state)
        self.source = self.root / 'source'; self.source.mkdir()
        (self.source / 'code.txt').write_text('baseline\n')
        git(self.source, 'init', '--initial-branch=main', '--template=')
        git(self.source, 'add', '--all'); git(self.source, 'commit', '-m', 'Base')
        base = git(self.source, 'rev-parse', 'HEAD')
        self.config = load_config(self.state)
        self.project = {'repository': str(self.source), 'base_commit': base, 'worker': 'codex', 'adapter': 'codex',
                        'scope': ['*'], 'permission_profile': 'coding-v1', 'verification': {'profile': 'repository-v1'},
                        'limits': {'runtime_seconds': 60, 'max_turns': 10, 'max_revisions': 5}, 'execution_enabled': True}
        self.link = self.root / 'linked-original'
        git(self.root, 'clone', '--no-local', '--template=', str(self.source), str(self.link))
        self.project['links'] = [{'name': 'client', 'repository': str(self.link), 'access': 'edit'}]
        self.config['projects']['app'] = self.project
        self.config['peers']['reader'] = {'enabled': True, 'projects': ['app']}
        self.store = Store(self.state); self.addCleanup(lambda: self.store.close())
        self.task = self.call('submit', {'project_id': 'app', 'recipient': 'codex', 'base_commit': base, 'objective': 'Update code',
                                       'scope': ['*'], 'context': [], 'acceptance': ['Review'], 'limits': self.project['limits'], 'permission_profile': 'coding-v1'})
        self.workspace = Workspace(self.state, self.task, self.project); self.workspace.prepare()
        (self.workspace.path / 'code.txt').write_text('saved change\n')
        result = self.workspace.result({'summary': 'Implemented', 'issues': []})
        self.store.update(self.task['id'], 'result', status='completed', result=result)
        (self.workspace.path / 'draft.txt').write_text('pending draft\n')

    def call(self, op, body, identity='local', key=None):
        return self.store.handle(request(op, body, key), identity, self.config)

    def review(self, identity='local'):
        return self.call('task_work', {'task_id': self.task['id'], **({'shared': True} if identity != 'local' else {})}, identity)

    def delete(self, snapshot, key=None):
        return self.call('task_forget', {'task_id': self.task['id'], 'delete_work': True, 'work_snapshot': snapshot}, key=key)

    def test_review_keep_or_delete_with_snapshot_and_original_project_preservation(self):
        helper = self.root / 'external-diff'
        marker = self.root / 'must-not-run'
        helper.write_text('#!/bin/sh\n/usr/bin/touch ' + str(marker) + '\n')
        helper.chmod(0o700)
        git(self.workspace.path, 'config', 'diff.external', str(helper))
        review = self.review()
        self.assertFalse(marker.exists(), 'Inspecting work ran a repository diff helper')
        files = {f['path']: f for f in review['repositories'][0]['files']}
        self.assertIn('saved change', files['code.txt']['preview'])
        self.assertIn('pending draft', files['draft.txt']['preview'])
        self.assertEqual(review['repositories'][0]['saved_commit_count'], 1)
        self.call('task_forget', {'task_id': self.task['id']}, identity='reader')
        self.assertTrue(self.workspace.path.exists())  # Keeping work is a personal history action.
        (self.workspace.path / 'draft.txt').write_text('newer draft\n')
        with self.assertRaisesRegex(BridgeError, 'changed after your preview'):
            self.delete(review['snapshot'])
        self.assertTrue(self.workspace.path.exists())
        review = self.review()
        from aabridge_core.task_work import preview
        def change_after_preview(*args):
            result = preview(*args)
            (self.workspace.path / 'draft.txt').write_text('changed immediately before staging\n')
            return result
        with patch('aabridge_core.task_work.preview', side_effect=change_after_preview):
            with self.assertRaisesRegex(BridgeError, 'changed after your preview'):
                self.delete(review['snapshot'])
        self.assertTrue(self.workspace.path.exists())
        review = self.review()
        deleted = self.delete(review['snapshot'], key='delete-confirmed')
        self.assertTrue(deleted['work_deleted'])
        self.assertFalse(deleted['cleanup_pending'])
        self.assertFalse(self.workspace.path.exists())
        self.assertFalse((self.state / 'workspaces' / (self.task['id'] + '.linked')).exists())
        self.assertTrue((self.link / 'code.txt').exists())
        self.assertEqual((self.source / 'code.txt').read_text(), 'baseline\n')
        self.assertTrue(self.store.history_page(self.task['id'], 0)['messages'])
        self.assertTrue(self.delete(review['snapshot'], key='delete-confirmed')['work_deleted'])
        with self.assertRaisesRegex(BridgeError, 'workspace was deleted'):
            self.call('revise', {'task_id': self.task['id'], 'revision': 1, 'text': 'Continue'})

    def test_active_shared_and_symlink_work_cannot_be_deleted(self):
        self.assertFalse(self.review('reader')['can_delete_work'])
        token = self.review()['snapshot']
        with self.assertRaises(BridgeError):
            self.call('task_forget', {'task_id': self.task['id'], 'delete_work': True, 'work_snapshot': token}, identity='reader')
        self.store.update(self.task['id'], 'worker_started', status='running', worker_pid=123)
        review = self.review()
        self.assertFalse(review['can_delete_work'])
        with self.assertRaises(BridgeError):
            self.delete(review['snapshot'])
        self.store.update(self.task['id'], 'result', status='completed', worker_pid=None)
        room = {'id': 'team-usage', 'objective': 'Continue saved work', 'status': 'running',
                'stage_tasks': [{'task_id': self.task['id'], 'target': 'local', 'stage': 'work'}]}
        self.store.db.execute('INSERT INTO teams VALUES (?,?,?)', (room['id'], 'usage-fixture', json.dumps(room)))
        review = self.review()
        self.assertEqual(review['team_usage'][0]['title'], 'Continue saved work')
        self.assertFalse(review['can_delete_work'])
        with self.assertRaises(BridgeError):
            self.delete(review['snapshot'])
        room['status'] = 'completed'
        self.store.db.execute('UPDATE teams SET document=? WHERE id=?', (json.dumps(room), room['id']))
        self.assertTrue(self.review()['can_delete_work'])
        parked = self.workspace.path.with_suffix('.parked'); self.workspace.path.rename(parked)
        self.workspace.path.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(BridgeError):
            self.review()
        self.assertTrue((self.source / 'code.txt').exists())
        self.workspace.path.unlink(); parked.rename(self.workspace.path)

    def test_database_failure_restores_work_and_cleanup_failure_can_be_retried(self):
        token = self.review()['snapshot']
        self.store.db.execute("CREATE TRIGGER reject_hide BEFORE INSERT ON task_preferences BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.delete(token)
        self.assertTrue(self.workspace.path.exists())
        self.assertFalse(self.store.get(self.task['id']).get('work_deleted'))
        self.store.db.execute('DROP TRIGGER reject_hide')
        # Simulate SQLite rollback after the process died with folders moved.
        from aabridge_core.task_work import stage
        self.store.db.execute('BEGIN IMMEDIATE')
        stage(self.store, self.store.get(self.task['id']), self.project, 'local', token)
        self.store.db.execute('ROLLBACK')
        self.store._staged_work = None
        self.store.close(); self.store = Store(self.state)
        self.assertTrue(self.workspace.path.exists())
        self.assertEqual((self.workspace.path / 'draft.txt').read_text(), 'pending draft\n')
        token = self.review()['snapshot']
        with patch('aabridge_core.task_work.shutil.rmtree', side_effect=OSError('fixture disk failure')) as remove:
            remove.avoids_symlink_attacks = True
            result = self.delete(token, key='disk-cleanup')
        self.assertTrue(result['cleanup_pending'])
        self.assertTrue(self.store.get(self.task['id'])['work_cleanup'])
        self.assertFalse(self.delete(token, key='disk-cleanup')['cleanup_pending'])
        self.assertIsNone(self.store.get(self.task['id'])['work_cleanup'])
