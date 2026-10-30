from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import configure_coding_project, request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_core.teams import TeamRunner, get, save
from aabridge_protocol.messages import BridgeError
from aabridge_workspace.git import Workspace, git


class TeamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-team-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        initialize(self.state)
        self.store = Store(self.state)
        self.addCleanup(self.store.close)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        (self.repo / 'README.md').write_text('Before\n')
        git(self.repo, 'init', '--initial-branch=main', '--template=')
        git(self.repo, 'add', '--all')
        git(self.repo, 'commit', '-m', 'Baseline')
        self.base = git(self.repo, 'rev-parse', 'HEAD')
        self.project = {'worker': 'codex', 'adapter': 'codex', 'base_commit': self.base,
                        'scope': ['*'], 'execution_enabled': True, 'verification': {'profile': 'repository-v1'},
                        'limits': {'runtime_seconds': 3600, 'max_turns': 100, 'max_revisions': 100}}
        self.accepted, self.requests = {}, []
        self.drop = False
        self.pending = False
        self.mismatch = False
        self.runner = TeamRunner(self.state, self.store)
        self.runner.call = self.call

    def start(self, reverse=False):
        body = {'objective': 'Build a useful app',
                'leader': {'target': 'mini' if reverse else 'local', 'agent': 'claude' if reverse else 'codex', 'project': 'app'},
                'follower': {'target': 'local' if reverse else 'mini', 'agent': 'codex' if reverse else 'claude', 'project': 'app'}}
        value = request('team_start', body, 'room-start')
        return self.store.handle(value, 'local', load_config(self.state)), value

    async def call(self, op, body, target, key=None):
        self.requests.append((op, body, target, key))
        if op == 'projects':
            return {'app': {**self.project, 'adapter': 'codex' if target == 'local' else 'claude',
                            'worker': 'codex' if target == 'local' else 'claude',
                            'base_commit': 'b' * 40 if self.mismatch and target == 'mini' else self.base}}
        if op == 'submit':
            if key not in self.accepted:
                task_id = 'task-' + str(len(self.accepted))
                path = self.state / 'workspaces' / task_id
                git(path.parent, 'clone', '--no-local', '--template=', str(self.repo), str(path))
                if body['mode'] == 'work':
                    (path / 'README.md').write_text('After\n')
                    git(path, 'add', '--all'); git(path, 'commit', '-m', 'Implementation')
                self.accepted[key] = {'id': task_id, 'revision': 1, 'status': 'running' if self.pending else 'completed',
                    'result': {'summary': 'Leader plan' if body['mode'] == 'plan' else 'Follower implementation' if body['mode'] == 'work' else 'Reviewed real diff',
                               'worktree': str(path), 'branch': 'codex/' + task_id, 'base_commit': self.base,
                               'result_commit': git(path, 'rev-parse', 'HEAD')}}
            if self.drop:
                self.drop = False
                raise BridgeError('remote_unavailable', 'Acknowledgement dropped')
            return self.accepted[key]
        if op == 'revise':
            task = next(t for t in self.accepted.values() if t['id'] == body['task_id'])
            if task['revision'] == body['revision']:
                path = Path(task['result']['worktree'])
                (path / 'second.txt').write_text('Second round')
                git(path, 'add', '--all'); git(path, 'commit', '-m', 'Second round')
                task['revision'] += 1
                task['result']['result_commit'] = git(path, 'rev-parse', 'HEAD')
            return task
        if op == 'status':
            return next(t for t in self.accepted.values() if t['id'] == body['task_id'])
        if op == 'cancel':
            task = next(t for t in self.accepted.values() if t['id'] == body['task_id'])
            task['status'] = 'cancelled'
            return task
        raise AssertionError(op)

    async def test_reverse_roles_and_real_diff_review(self):
        for reverse in (False, True):
            self.store.db.execute('DELETE FROM teams')
            self.accepted.clear(); self.requests.clear()
            import shutil
            if (self.state / "workspaces").exists():
                shutil.rmtree(self.state / "workspaces")
            (self.state / "workspaces").mkdir(mode=0o700)
            room, value = self.start(reverse)
            # Remote fetch is exercised independently by the SSH integration suite.
            def fetched(state, target, task_id):
                task = next(t for t in self.accepted.values() if t['id'] == task_id)
                result = task['result']
                return {'diff': git(Path(result['worktree']), 'diff', self.base, result['result_commit'])}
            with patch('aabridge_cli.actions.fetch_result', side_effect=fetched):
                for _ in range(3):
                    await self.runner.step(room['id'])
            done = get(self.store, room['id'])
            self.assertEqual(done['status'], 'completed', done)
            submits = [r for r in self.requests if r[0] == 'submit']
            self.assertEqual([r[2] for r in submits], ['mini', 'local', 'mini'] if reverse else ['local', 'mini', 'local'])
            self.assertIn('Leader plan', submits[1][1]['context'][0])
            self.assertIn('+After', submits[2][1]['context'][2])
            self.assertEqual((self.repo / 'README.md').read_text(), 'Before\n')
            replay = self.store.handle(value, 'local', load_config(self.state))
            self.assertEqual(replay['id'], room['id'])

    async def test_followup_revises_the_existing_implementation(self):
        room, _ = self.start(reverse=True)
        for _ in range(3):
            await self.runner.step(room['id'])
        before = get(self.store, room['id'])['outputs']['work']['worktree']
        value = request('team_reply', {'team_id':room['id'], 'text':'Now add a second feature'}, 'next-round')
        self.store.handle(value, 'local', {})
        for _ in range(3):
            await self.runner.step(room['id'])
        after = get(self.store, room['id'])
        self.assertEqual(after['status'], 'completed', after)
        self.assertEqual(after['outputs']['work']['worktree'], before)
        self.assertEqual((Path(before) / 'README.md').read_text(), 'After\n')
        self.assertTrue((Path(before) / 'second.txt').exists())
        self.assertEqual(len([r for r in self.requests if r[0] == 'revise']), 1)

    async def test_model_selection_blocks_old_participant_before_starting_any_agent(self):
        room, _ = self.start()
        stored = get(self.store, room['id'])
        stored['leader']['model_settings'] = {'model':'selected-model','effort':'high'}
        save(self.store, stored)
        await self.runner.step(room['id'])
        blocked = get(self.store, room['id'])
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('older AABridge', blocked['error'])
        self.assertFalse(self.accepted)

    async def test_model_settings_are_pinned_independently_for_each_role(self):
        room, _ = self.start()
        stored = get(self.store, room['id'])
        lead = {'model':'leader-model','effort':'high'}
        follow = {'model':'follower-model','effort':'low'}
        stored['leader']['model_settings'] = lead; stored['follower']['model_settings'] = follow
        save(self.store, stored)
        self.project['capabilities'] = {'model_settings':True}
        self.pending = True
        await self.runner.step(room['id'])
        self.assertEqual([r for r in self.requests if r[0] == 'submit'][0][1]['model_settings'], lead)
        next(iter(self.accepted.values()))['status'] = 'completed'
        await self.runner.step(room['id'])
        await self.runner.step(room['id'])
        self.assertEqual([r for r in self.requests if r[0] == 'submit'][1][1]['model_settings'], follow)

    async def test_live_feed_survives_cursor_replay_and_does_not_replay_model_work(self):
        room, _ = self.start()
        self.project['capabilities'] = {'activity':True}
        self.pending = True
        original = self.runner.call
        async def streamed(op, body, target, key=None):
            if op == 'activity':
                entries = [] if body['cursor'] else [{'cursor':1,'id':'tool-1','kind':'tool','text':'Reading a file: app.py','state':'running','task_id':body['task_id'],'revision':1,'time':1234.0}]
                return {'entries':entries,'cursor':1,'has_more':False}
            return await original(op, body, target, key)
        self.runner.call = streamed
        await self.runner.step(room['id'])
        page = self.store.handle(request('team_activity',{'team_id':room['id'],'cursor':0}),'local',{})
        self.assertEqual(len(page['entries']),1)
        self.assertEqual(page['entries'][0]['role'],'leader')
        stored = get(self.store,room['id']); stored['activity_cursors'] = {}; save(self.store,stored)
        await self.runner.step(room['id'])
        replay = self.store.handle(request('team_activity',{'team_id':room['id'],'cursor':0}),'local',{})
        self.assertEqual(len(replay['entries']),1)
        self.assertEqual(len(self.accepted),1)
        with self.assertRaises(BridgeError):
            self.store.handle(request('team_activity',{'team_id':room['id'],'cursor':0}),'mini',{})

    async def test_activity_failure_keeps_the_model_task_running(self):
        room, _ = self.start()
        self.project['capabilities'] = {'activity':True}
        self.pending = True
        await self.runner.step(room['id'])  # Fixture rejects activity; runner must tolerate it.
        stored = get(self.store,room['id'])
        self.assertEqual(stored['status'],'running')
        self.assertIn('task continues',stored['live']['stream_error'])
        self.assertEqual(len(self.accepted),1)

    async def test_lost_ack_restart_reuses_stage_key(self):
        room, _ = self.start()
        self.drop = True
        await self.runner.step(room['id'])
        self.assertEqual(get(self.store, room['id'])['status'], 'blocked')
        self.store.handle(request('team_retry', {'team_id': room['id']}, 'retry-one'), 'local', {})
        restarted = TeamRunner(self.state, self.store)
        restarted.call = self.call
        await restarted.step(room['id'])
        self.assertEqual(len(self.accepted), 1)
        self.assertEqual(get(self.store, room['id'])['stage'], 'work')

    async def test_stop_recovers_lost_ack_before_cancelling(self):
        room, _ = self.start()
        self.drop, self.pending = True, True
        await self.runner.step(room['id'])
        self.store.handle(request('team_cancel', {'team_id': room['id']}, 'stop-one'), 'local', {})
        await self.runner.step(room['id'])
        await self.runner.step(room['id'])
        self.assertEqual(get(self.store, room['id'])['status'], 'cancelled')
        self.assertEqual(len(self.accepted), 1)
        self.assertEqual(next(iter(self.accepted.values()))['status'], 'cancelled')

    async def test_stop_during_role_lookup_starts_no_agent(self):
        room, _ = self.start()
        original = self.runner.call
        stopped = False
        async def during_lookup(op, body, target, key=None):
            nonlocal stopped
            if op == 'projects' and not stopped:
                stopped = True
                self.store.handle(request('team_cancel', {'team_id':room['id']}, 'stop-lookup'), 'local', {})
            return await original(op, body, target, key)
        self.runner.call = during_lookup
        await self.runner.step(room['id'])
        self.assertEqual(get(self.store, room['id'])['status'], 'cancelled')
        self.assertFalse(self.accepted)
        self.assertTrue(any(m['text'] == 'Stop this task.' for m in get(self.store, room['id'])['messages']))

    async def test_mismatched_baselines_start_no_agent(self):
        room, _ = self.start()
        self.mismatch = True
        await self.runner.step(room['id'])
        self.assertEqual(get(self.store, room['id'])['status'], 'blocked')
        self.assertFalse(self.accepted)

    async def test_peer_cannot_assign_roles_and_idempotency_conflicts(self):
        room, value = self.start()
        with self.assertRaises(BridgeError):
            self.store.handle(value, 'mini', {})
        changed = dict(value.body, objective='Something else')
        with self.assertRaises(BridgeError):
            self.store.handle(request('team_start', changed, value.key), 'local', {})
        reply = request('team_cancel', {'team_id': room['id']}, 'same-control')
        self.store.handle(reply, 'local', {})
        self.store.handle(reply, 'local', {})
        self.assertEqual(len([m for m in get(self.store, room['id'])['messages'] if m['text'] == 'Stop this task.']), 1)


class CodingWorkspaceTests(unittest.TestCase):
    def test_whole_project_add_delete_large_hidden_files_and_legacy_denial(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-code-') as tmp:
            root = Path(tmp).resolve(); state = root / 'state'; repo = root / 'repo'
            initialize(state); repo.mkdir()
            (repo / 'old.txt').write_text('original')
            git(repo, 'init', '--initial-branch=main', '--template='); git(repo, 'add', '--all'); git(repo, 'commit', '-m', 'Base')
            with patch('aabridge_cli.actions.runtime_inventory', return_value={'codex': {'status': 'found', 'executable': sys.executable}}):
                configure_coding_project(state, 'app', repo, [], 'codex')
            config = load_config(state); project = config['projects']['app']
            body = {'project_id': 'app', 'recipient': 'codex', 'base_commit': project['base_commit'], 'objective': 'Build', 'scope': ['*'],
                    'context': [], 'acceptance': ['Review'], 'limits': project['limits'], 'permission_profile': 'coding-v1'}
            store = Store(state)
            try:
                task = store.handle(request('submit', body), 'local', config)
                work = Workspace(state, task, project)
                self.assertEqual(work.prepare(), {})
                (work.path / 'old.txt').unlink()
                (work.path / '.github').mkdir()
                (work.path / '.github/workflow.yml').write_text('x' * 100000)
                result = work.result({'summary': 'Added and deleted', 'issues': []})
                self.assertEqual(result['changed_paths'], ['.github/workflow.yml', 'old.txt'])
                self.assertEqual(git(work.path, 'remote'), '')
                self.assertEqual((repo / 'old.txt').read_text(), 'original')
                with self.assertRaises(BridgeError):
                    store.handle(request('submit', dict(body, permission_profile='proposal-v1', scope=['old.txt'])), 'local', config)
            finally:
                store.close()
