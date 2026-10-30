from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_core.teams import TeamRunner, get, verdict
from aabridge_protocol.messages import BridgeError, validate_request
from aabridge_workspace import publish
from aabridge_workspace.git import git


class AutonomyTests(unittest.IsolatedAsyncioTestCase):
    """Leader-owned review/fix loops, admin gates, notes and result delivery."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-auto-')
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
        config = load_config(self.state)
        config['projects']['app'] = {'repository': str(self.repo)}
        save_config(self.state, config)
        self.project = {'worker': 'codex', 'adapter': 'codex', 'base_commit': self.base,
                        'scope': ['*'], 'execution_enabled': True, 'verification': {'profile': 'repository-v1'},
                        'limits': {'runtime_seconds': 3600, 'max_turns': 100, 'max_revisions': 100}}
        self.tasks, self.requests = {}, []
        self.reviews = []  # Summaries the leader returns, one per review turn.
        self.pending = False
        self.runner = TeamRunner(self.state, self.store)
        self.runner.call = self.call

    def start(self, **options):
        body = {'objective': 'Add a weight input screen',
                'leader': {'target': 'mini', 'agent': 'codex', 'project': 'app'},
                'follower': {'target': 'local', 'agent': 'claude', 'project': 'app'}, **options}
        return self.store.handle(request('team_start', body, 'room-' + str(len(self.requests))), 'local', load_config(self.state))

    def admin(self, op, body):
        return self.store.handle(request(op, body, op + '-' + str(len(self.requests)) + '-' + str(len(get(self.store, body['team_id'])['controls']))), 'local', {})

    def turn(self, task, mode):
        path = Path(task['result']['worktree'])
        if mode == 'work':
            (path / f'change-{task["revision"]}.txt').write_text('work')
            git(path, 'add', '--all'); git(path, 'commit', '-m', 'Work ' + str(task['revision']))
        summary = {'plan': 'Plan v' + str(task['revision']), 'work': 'Implemented v' + str(task['revision'])}.get(mode)
        task['result'].update(summary=summary or (self.reviews.pop(0) if self.reviews else 'Looks fine'),
                              result_commit=git(path, 'rev-parse', 'HEAD'))

    async def call(self, op, body, target, key=None):
        self.requests.append((op, body, target, key))
        if op == 'projects':
            return {'app': {**self.project, 'adapter': 'codex' if target == 'mini' else 'claude', 'worker': 'codex' if target == 'mini' else 'claude'}}
        if op == 'submit':
            if key not in self.tasks:
                task_id = 'task-' + str(len(self.tasks))
                path = self.state / 'workspaces' / task_id
                git(path.parent, 'clone', '--no-local', '--template=', str(self.repo), str(path))
                git(path, 'checkout', '-q', '-b', ('claude/' if target == 'local' else 'codex/') + task_id)
                task = {'id': task_id, 'revision': 1, 'mode': body['mode'], 'status': 'running' if self.pending else 'completed', 'text': [body['objective'], *body['context']],
                        'result': {'worktree': str(path), 'branch': 'x', 'base_commit': self.base}}
                self.turn(task, body['mode'])
                self.tasks[key] = task
            return self.tasks[key]
        task = next(t for t in self.tasks.values() if t['id'] == body['task_id'])
        if op == 'revise':
            if task['revision'] == body['revision']:
                task['revision'] += 1
                task['text'] = [body['text']]
                self.turn(task, task['mode'])
            return task
        if op == 'status':
            return task
        if op == 'cancel':
            task['status'] = 'cancelled'
            return task
        raise AssertionError(op)

    async def run_until(self, room_id, statuses=('completed', 'awaiting_admin', 'failed', 'blocked'), limit=20):
        for _ in range(limit):
            await self.runner.step(room_id)
            room = get(self.store, room_id)
            if room['status'] in statuses:
                return room
        self.fail('Team did not settle: ' + room['status'] + ' ' + room['stage'] + ' ' + repr(self.stages()) + repr([m['text'][:80] for m in room['messages']]))

    def stages(self):
        return [(op, body.get('mode') or next(t['mode'] for t in self.tasks.values() if t['id'] == body['task_id']))
                for op, body, _, _ in self.requests if op in {'submit', 'revise'}]

    def test_verdict_reads_the_final_marker(self):
        self.assertEqual(verdict('Fine.\nVERDICT: APPROVED'), 'approved')
        self.assertEqual(verdict('VERDICT: APPROVED\n...\n**VERDICT: CHANGES_REQUESTED**\n1. Fix the bug'), 'changes')
        self.assertIsNone(verdict('Looks good to me'))

    def test_new_options_are_validated(self):
        base = {'objective': 'x', 'leader': {'target': 'local', 'project': 'a', 'agent': 'codex'}, 'follower': {'target': 'mini', 'project': 'a', 'agent': 'claude'}}
        envelope = lambda op, body: {'version': 1, 'op': op, 'key': 'k1', 'body': body}
        validate_request(envelope('team_start', {**base, 'approval': 'auto', 'max_fixes': 4, 'placement': {'mode': 'worktree', 'pull_request': True}}))
        for bad in ({'approval': 'yolo'}, {'max_fixes': 0}, {'placement': {'mode': 'main'}}, {'placement': {'mode': 'sandbox', 'pull_request': True}}):
            with self.assertRaises(BridgeError):
                validate_request(envelope('team_start', {**base, **bad}))
        validate_request(envelope('team_guide', {'team_id': 'team-1', 'text': 'Use metric units'}))
        validate_request(envelope('team_decide', {'team_id': 'team-1', 'decision': 'replan', 'text': 'Smaller'}))
        with self.assertRaises(BridgeError):
            validate_request(envelope('team_decide', {'team_id': 'team-1', 'decision': 'merge'}))

    async def test_auto_mode_loops_review_and_fix_until_the_leader_approves(self):
        self.reviews = ['1. Negative weights are accepted.\nVERDICT: CHANGES_REQUESTED', 'Fixed properly.\nVERDICT: APPROVED']
        room = self.start(approval='auto')
        done = await self.run_until(room['id'])
        self.assertEqual(done['status'], 'completed', done['messages'][-1])
        self.assertEqual(self.stages(), [('submit', 'plan'), ('submit', 'work'), ('submit', 'review'), ('revise', 'work'), ('revise', 'review')])
        fix = [b for op, b, _, _ in self.requests if op == 'revise'][0]['text']
        self.assertIn('Negative weights are accepted', fix)
        self.assertIn('explain why with evidence', fix)
        rereview = [b for op, b, _, _ in self.requests if op == 'revise'][1]['text']
        self.assertIn('change-2.txt', rereview)  # The leader re-reviews the real, cumulative diff.
        self.assertEqual(done['fixes'], 1)
        self.assertIn('leader approved', done['messages'][-1]['text'])

    async def test_auto_mode_asks_the_admin_after_the_fix_limit(self):
        self.reviews = ['VERDICT: CHANGES_REQUESTED'] * 3
        room = self.start(approval='auto', max_fixes=2)
        waiting = await self.run_until(room['id'])
        self.assertEqual(waiting['status'], 'awaiting_admin')
        self.assertEqual(waiting['gate']['kind'], 'review')
        self.assertEqual(waiting['fixes'], 2)
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'finish'})
        self.assertEqual(get(self.store, room['id'])['status'], 'completed')

    async def test_ask_mode_waits_for_plan_approval_and_fix_approval(self):
        self.reviews = ['VERDICT: CHANGES_REQUESTED\n1. Add a label', 'VERDICT: APPROVED']
        room = self.start(approval='ask')
        waiting = await self.run_until(room['id'])
        self.assertEqual((waiting['status'], waiting['gate']['kind']), ('awaiting_admin', 'plan'))
        self.assertEqual(self.stages(), [('submit', 'plan')])
        with self.assertRaises(BridgeError):
            self.admin('team_decide', {'team_id': room['id'], 'decision': 'replan'})  # Needs a note.
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'replan', 'text': 'Use kilograms'})
        waiting = await self.run_until(room['id'])
        self.assertEqual(self.stages()[-1], ('revise', 'plan'))
        self.assertIn('Use kilograms', [b for op, b, _, _ in self.requests if op == 'revise'][-1]['text'])
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'continue'})
        waiting = await self.run_until(room['id'])
        self.assertEqual((waiting['status'], waiting['gate']['kind']), ('awaiting_admin', 'review'))
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'continue', 'text': 'Also round to one decimal'})
        done = await self.run_until(room['id'])
        self.assertEqual(done['status'], 'completed')
        fix = [b for op, b, _, _ in self.requests if op == 'revise' and 'requested changes' in b['text']][0]['text']
        self.assertIn('Add a label', fix)
        self.assertIn('round to one decimal', fix)

    async def test_admin_note_mid_task_reaches_the_working_agent(self):
        self.reviews = ['VERDICT: APPROVED']
        self.pending = True
        room = self.start(approval='auto')
        await self.runner.step(room['id'])  # Plan running.
        self.admin('team_guide', {'team_id': room['id'], 'text': 'Store weight in grams'})
        self.assertEqual(get(self.store, room['id'])['guidance'][0]['delivered'], False)
        self.pending = False
        for task in self.tasks.values():
            task['status'] = 'completed'
        await self.runner.step(room['id'])
        replan = await self.run_until(room['id'], statuses=('completed',))
        texts = [b.get('text') or b['objective'] + ''.join(b['context']) for op, b, _, _ in self.requests if op in {'submit', 'revise'}]
        self.assertEqual(self.stages()[:2], [('submit', 'plan'), ('revise', 'plan')])
        self.assertIn('Store weight in grams', texts[1])
        self.assertTrue(all(note['delivered'] for note in replan['guidance']))
        with self.assertRaises(BridgeError):
            self.admin('team_guide', {'team_id': room['id'], 'text': 'Too late'})

    async def test_note_while_working_starts_an_adjustment_turn_before_review(self):
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto')
        await self.runner.step(room['id'])  # Plan finished; work is next.
        self.pending = True
        await self.runner.step(room['id'])  # Work running.
        self.admin('team_guide', {'team_id': room['id'], 'text': 'Make the button blue'})
        self.pending = False
        for task in self.tasks.values():
            task['status'] = 'completed'
        await self.run_until(room['id'], statuses=('completed',))
        self.assertEqual(self.stages(), [('submit', 'plan'), ('submit', 'work'), ('revise', 'work'), ('submit', 'review')])
        self.assertIn('Make the button blue', [b for op, b, _, _ in self.requests if op == 'revise'][0]['text'])

    async def test_new_branch_placement_copies_the_result_without_touching_main(self):
        self.reviews = ['VERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'branch'})
        done = await self.run_until(room['id'])
        branch = done['publication']['branch']
        self.assertTrue(branch.startswith('claude/add-a-weight-input-screen-'), branch)
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/heads/' + branch), done['outputs']['work']['result_commit'])
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.base)
        self.assertEqual(git(self.repo, 'branch', '--show-current'), 'main')
        self.assertFalse((self.repo / 'change-1.txt').exists())

    async def test_worktree_placement_and_branch_name_collision(self):
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'worktree'})
        name = publish.branch_name('claude', 'Add a weight input screen', room['id'])
        git(self.repo, 'branch', name)  # Someone else's branch with the same name.
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], name + '-2')
        self.assertTrue((Path(done['publication']['path']) / 'change-1.txt').exists())
        self.assertEqual(git(self.repo, 'rev-parse', name), self.base)

    async def test_current_branch_placement_refuses_main_and_fast_forwards_a_feature_branch(self):
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'current'})
        done = await self.run_until(room['id'])
        self.assertIn('never commits to a main branch', done['publication']['error'])
        self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.base)
        git(self.repo, 'checkout', '-q', '-b', 'feature/weights')
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'current'})
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], 'feature/weights')
        self.assertTrue((self.repo / 'change-1.txt').exists())

    async def test_pull_request_is_opened_reviewed_and_merged_only_on_request(self):
        self.reviews = ['VERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        calls = []
        def opened(repo, branch, title, body, existing=None):
            calls.append(('pr', branch, existing))
            return {'url': 'https://github.com/o/r/pull/7', 'base': 'main'}
        with patch('aabridge_workspace.publish.pull_request', side_effect=opened), \
             patch('aabridge_workspace.publish.comment', side_effect=lambda *a: calls.append(('comment',))), \
             patch('aabridge_workspace.publish.merge', side_effect=lambda *a: calls.append(('merge',))):
            room = self.start(approval='auto', placement={'mode': 'branch', 'pull_request': True})
            done = await self.run_until(room['id'])
            self.assertEqual(done['publication']['pull_request'], 'https://github.com/o/r/pull/7')
            self.assertEqual([c[0] for c in calls], ['pr', 'comment', 'pr', 'comment'])
            self.assertEqual(calls[2][2], 'https://github.com/o/r/pull/7')  # The fix updates the same PR.
            self.assertNotIn(('merge',), calls)
            self.admin('team_merge', {'team_id': room['id']})
            await self.runner.step(room['id'])
            self.assertEqual(get(self.store, room['id'])['publication']['merge'], 'merged')
            self.assertIn(('merge',), calls)

    async def test_placement_in_the_project_needs_a_member_on_this_mac(self):
        body = {'objective': 'x', 'leader': {'target': 'mini', 'agent': 'codex', 'project': 'app'},
                'follower': {'target': 'book', 'agent': 'claude', 'project': 'app'}, 'placement': {'mode': 'branch'}}
        with self.assertRaises(BridgeError):
            self.store.handle(request('team_start', body, 'remote-only'), 'local', {})

    async def test_stop_while_waiting_for_the_admin(self):
        room = self.start(approval='ask')
        await self.run_until(room['id'])
        self.admin('team_cancel', {'team_id': room['id']})
        await self.runner.step(room['id'])
        self.assertEqual(get(self.store, room['id'])['status'], 'cancelled')

    def test_pull_request_pushes_the_feature_branch_and_reuses_an_open_pr(self):
        origin = self.root / 'origin.git'
        git(self.root, 'init', '--bare', '--template=', str(origin))
        git(self.repo, 'remote', 'add', 'origin', str(origin))
        git(self.repo, 'push', '-q', 'origin', 'main')
        git(self.repo, 'branch', 'claude/weights', self.base)
        log = self.root / 'gh.log'
        fake = self.root / 'gh'
        fake.write_text('#!/bin/sh\necho "$@" >> ' + str(log) + '\n'
                        'case "$1 $2" in "repo view") echo main;; "pr list") cat ' + str(self.root / 'open') + ' 2>/dev/null || true;; '
                        '"pr create") cat > /dev/null; echo https://github.com/o/r/pull/9;; esac\n')
        fake.chmod(0o755)
        with patch('aabridge_workspace.publish.GH', (str(fake),)):
            opened = publish.pull_request(self.repo, 'claude/weights', 'Claude: weights', 'Body')
            self.assertEqual(opened, {'url': 'https://github.com/o/r/pull/9', 'base': 'main'})
            self.assertEqual(git(origin, 'rev-parse', 'refs/heads/claude/weights'), self.base)
            (self.root / 'open').write_text('https://github.com/o/r/pull/9\n')
            self.assertEqual(publish.pull_request(self.repo, 'claude/weights', 'Claude: weights', 'Body')['url'], 'https://github.com/o/r/pull/9')
            with self.assertRaises(BridgeError):
                publish.pull_request(self.repo, 'main', 'x', 'y')
        self.assertEqual(log.read_text().count('pr create'), 1)

    async def test_updated_agent_names_the_mac_and_project_and_retry_starts_fresh(self):
        config = load_config(self.state)
        config['remotes']['mini'] = {'label': 'Sparsh’s MBP', 'host': 'mbp.local', 'user': 'me', 'port': 22, 'identity_file': '/x', 'known_hosts': '/y'}
        save_config(self.state, config)
        original = self.call
        broken = True
        async def updated(op, body, target, key=None):
            task = await original(op, body, target, key)
            if op == 'submit' and broken:
                task.update(status='failed', error_code='adapter_changed', error='The agent was updated. Save this project again to revalidate it.')
            return task
        self.runner.call = updated
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto')
        blocked = await self.run_until(room['id'])
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual((blocked['failure']['machine'], blocked['failure']['project']), ('Sparsh’s MBP', 'app'))
        self.assertIn('Codex was updated on Sparsh’s MBP', blocked['messages'][-1]['text'])
        self.assertIn('On Sparsh’s MBP, open AABridge → Projects, select “app” and press Save', blocked['messages'][-1]['text'])
        self.assertEqual(blocked['messages'][-1]['speaker'], 'AABridge')  # Not shown as the leader talking.
        broken = False
        self.admin('team_retry', {'team_id': room['id']})
        done = await self.run_until(room['id'], statuses=('completed',))
        self.assertIsNone(done.get('failure'))
        plan_keys = [key for op, body, _, key in self.requests if op == 'submit' and body['mode'] == 'plan']
        self.assertEqual(len(set(plan_keys)), 2)  # The retry made a new task instead of replaying the failed one.

    async def test_unreachable_mac_is_named(self):
        async def offline(op, body, target, key=None):
            raise BridgeError('remote_unavailable', 'SSH authentication, reachability, or restricted entry point failed; no raw SSH output returned.')
        self.runner.call = offline
        room = self.start(approval='auto')
        await self.runner.step(room['id'])
        blocked = get(self.store, room['id'])
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('Couldn’t reach AABridge on mini', blocked['error'])
        self.assertIn('Remote Login', blocked['error'])

    async def test_direct_connection_failure_does_not_mention_remote_login(self):
        async def offline(op, body, target, key=None):
            raise BridgeError('direct_unavailable', 'Direct connection failed.')
        self.runner.call = offline
        room = self.start(approval='auto')
        await self.runner.step(room['id'])
        error = get(self.store, room['id'])['error']
        self.assertIn('over its direct connection', error)
        self.assertNotIn('Remote Login', error)

    def test_branch_names_are_short_and_valid(self):
        name = publish.branch_name('codex', 'Add a NEW screen — that takes weight as input!!! and more words here', 'team-0123456789abcdef')
        self.assertEqual(name, 'codex/add-a-new-screen-that-takes-weight-as-abcdef')
        self.assertEqual(publish.branch_name('claude', '✨', 'team-aaaaaa'), 'claude/task-aaaaaa')


if __name__ == '__main__':
    unittest.main()
