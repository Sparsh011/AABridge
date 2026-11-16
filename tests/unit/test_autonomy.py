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
                        'capabilities': {'review_snapshot': True, 'task_titles': True}, 'scope': ['*'], 'execution_enabled': True, 'verification': {'profile': 'repository-v1'},
                        'limits': {'runtime_seconds': 3600, 'max_turns': 100, 'max_revisions': 100}}
        self.tasks, self.requests = {}, []
        self.reviews = []  # Summaries the leader returns, one per review turn.
        self.pending = False
        self.idle_work = False  # A fix round that disputes the review and commits nothing.
        self.plans = []  # Leader plan-turn output, when a test needs a specific one.
        self.asks = None  # A stage whose agent stops to ask the admin a question first.
        self.parts, self.uploaded, self.big = {}, [], False  # Review bundles sent in parts; whether the worker writes a large file.
        self.branch_lines = {}  # Extra output line per stage, e.g. a BRANCH_NAME: choice.
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
        if mode == 'work' and self.big:
            import os
            (path / f'asset-{task["revision"]}.bin').write_bytes(os.urandom(130_000))
        if mode == 'work' and not self.idle_work:
            (path / f'change-{task["revision"]}.txt').write_text('work')
            git(path, 'add', '--all'); git(path, 'commit', '-m', 'Work ' + str(task['revision']))
        summary = {'plan': 'TASK_TITLE: Fix weight validation\nPlan v' + str(task['revision']), 'work': 'Implemented v' + str(task['revision'])}.get(mode)
        if mode == 'plan' and self.plans:
            summary = self.plans.pop(0)
        if mode in self.branch_lines:
            summary += '\n' + self.branch_lines[mode]
        task['result'].update(summary=summary or (self.reviews.pop(0) if self.reviews else 'Looks fine'),
                              result_commit=git(path, 'rev-parse', 'HEAD'))

    async def call(self, op, body, target, key=None):
        self.requests.append((op, body, target, key))
        if op == 'projects':
            return {'app': {**self.project, 'adapter': 'codex' if target == 'mini' else 'claude', 'worker': 'codex' if target == 'mini' else 'claude'}}
        if op == 'snapshot_part':
            import base64
            self.parts.setdefault(body['upload'], {})[body['index']] = base64.b64decode(body['data'])
            return {}
        if op in {'submit', 'revise'} and (body.get('review_snapshot') or {}).get('upload'):
            # Stand in for the receiving service: assemble the parts into the bundle the review imports.
            import base64, hashlib
            snapshot = body['review_snapshot']
            raw = b''.join(self.parts[snapshot['upload']][index] for index in range(snapshot['parts']))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), snapshot['sha256'])
            self.uploaded.append(len(raw))
            (self.state / 'snapshots').mkdir(exist_ok=True)
            (self.state / 'snapshots' / (snapshot['sha256'] + '.bundle')).write_bytes(raw)
            body = {**body, 'review_snapshot': {'commit': snapshot['commit'], 'bundle': '', 'file': snapshot['sha256']}}
        if op == 'submit':
            if key not in self.tasks:
                task_id = 'task-' + str(len(self.tasks))
                path = self.state / 'workspaces' / task_id
                git(path.parent, 'clone', '--no-local', '--template=', str(self.repo), str(path))
                git(path, 'checkout', '-q', '-b', ('claude/' if target == 'local' else 'codex/') + task_id)
                task = {'id': task_id, 'revision': 1, 'mode': body['mode'], 'status': 'running' if self.pending else 'completed', 'text': [body['objective'], *body['context']],
                        'result': {'worktree': str(path), 'branch': 'x', 'base_commit': self.base}}
                if body.get('review_snapshot'):
                    from aabridge_workspace.review import import_snapshot
                    import_snapshot(path, self.base, body['review_snapshot'], 'codex/' + task_id)
                if self.asks == body['mode']:
                    task.update(status='waiting_question', question={'id': 'q1', 'text': 'Kilograms or pounds?'})
                else:
                    self.turn(task, body['mode'])
                self.tasks[key] = task
            return self.tasks[key]
        task = next(t for t in self.tasks.values() if t['id'] == body['task_id'])
        if op == 'revise':
            if task['revision'] == body['revision']:
                task['revision'] += 1
                task['text'] = [body['text']]
                if body.get('review_snapshot'):
                    from aabridge_workspace.review import import_snapshot
                    import_snapshot(Path(task['result']['worktree']), self.base, body['review_snapshot'], 'codex/' + task['id'])
                self.turn(task, task['mode'])
            return task
        if op == 'answer':
            if task.get('question'):
                self.assertEqual((body['question_id'], body['revision']), (task['question']['id'], task['revision']))
                task.update(status='completed', question=None, answer=body['text'])
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
        from aabridge_protocol.titles import agent_branch, compact_title, leader_title
        self.assertEqual(leader_title('TASK_TITLE: Fix relay shutdown', 'Long instructions'), 'Fix relay shutdown')
        self.assertEqual(leader_title('TASK_TITLE: ' + 'x' * 81, 'Fix relay shutdown'), 'Fix relay shutdown')
        self.assertEqual(agent_branch('Plan\nBRANCH_NAME: first-name\nBRANCH_NAME: `weight-screen`\n'), 'weight-screen')
        self.assertEqual(agent_branch('BRANCH_NAME: good-name\nBRANCH_NAME: ../../main'), 'good-name')
        for bad in ('main;rm -rf', 'feature/weights', 'Weight-Screen', '-leading', 'trailing-', 'two--dashes', 'a.lock', 'x' * 49, ''):
            self.assertIsNone(agent_branch('BRANCH_NAME: ' + bad), bad)
        self.assertIsNone(agent_branch('Mentions BRANCH_NAME: inline-only'))
        self.assertEqual(compact_title('You are the follower. Admin task: Fix relay shutdown. Then test it.'), 'Fix relay shutdown')

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

    async def test_worktree_placement_and_branch_name_collision(self):
        self.reviews = ['VERDICT: APPROVED']
        self.branch_lines = {'plan': 'BRANCH_NAME: leader-choice', 'work': 'BRANCH_NAME: weight-screen'}
        room = self.start(approval='auto', placement={'mode': 'worktree'})
        name = 'claude/weight-screen-' + room['id'][-6:]
        git(self.repo, 'branch', name)  # Someone else's branch with the same name.
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], name + '-2')
        self.assertTrue((Path(done['publication']['path']) / 'change-1.txt').exists())
        self.assertEqual(git(self.repo, 'rev-parse', name), self.base)

    async def test_branch_name_falls_back_without_using_the_prompt(self):
        self.branch_lines = {'plan': 'BRANCH_NAME: leader-choice', 'work': 'BRANCH_NAME: ../../main'}
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'branch'})
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], 'claude/leader-choice-' + room['id'][-6:])
        self.branch_lines, self.reviews = {'plan': 'BRANCH_NAME: Not Valid'}, ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'branch'})
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], 'claude/task-' + room['id'][-6:])

    async def test_branch_stays_named_across_fix_rounds_and_only_when_delivery_needs_one(self):
        self.branch_lines = {'work': 'BRANCH_NAME: first-name'}
        self.reviews = ['VERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'branch'})
        done = await self.run_until(room['id'])
        self.assertEqual(done['publication']['branch'], 'claude/first-name-' + room['id'][-6:])
        asked = [b for op, b, _, _ in self.requests if op == 'submit' and 'BRANCH_NAME:' in b['objective']]
        self.assertEqual([b['mode'] for b in asked], ['plan', 'work'])
        self.reviews = ['VERDICT: APPROVED']
        await self.run_until(self.start(approval='auto')['id'])
        self.assertEqual(len([b for op, b, _, _ in self.requests if op == 'submit' and 'BRANCH_NAME:' in b['objective']]), 2)

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
        self.reviews = ['1. Negative weights are accepted.\nVERDICT: CHANGES_REQUESTED', 'Fixed properly.\nVERDICT: APPROVED']
        calls = []
        def opened(repo, branch, title, body, existing=None):
            calls.append(('pr', branch, existing))
            return {'url': 'https://github.com/o/r/pull/7', 'base': 'main'}
        with patch('aabridge_workspace.publish.pull_request', side_effect=opened), \
             patch('aabridge_workspace.publish.review', side_effect=lambda *a: calls.append(('comment',))), \
             patch('aabridge_workspace.publish.merge', side_effect=lambda *a: calls.append(('merge',))) as merge:
            room = self.start(approval='auto', placement={'mode': 'branch', 'pull_request': True})
            done = await self.run_until(room['id'])
            self.assertEqual(self.stages(), [('submit', 'plan'), ('submit', 'work'), ('submit', 'review'), ('revise', 'work'), ('revise', 'review')])
            work = next(body for op, body, _, _ in self.requests if op == 'submit' and body['mode'] == 'work')
            self.assertEqual(work['title'], 'Fix weight validation')
            self.assertIn('You are the follower.', work['objective'])
            self.assertEqual(done['title'], work['title'])
            revisions = [body for op, body, _, _ in self.requests if op == 'revise']
            self.assertIn('Negative weights are accepted', revisions[0]['text'])
            self.assertIn('explain why with evidence', revisions[0]['text'])
            self.assertEqual(revisions[1]['review_snapshot']['commit'], done['outputs']['work']['result_commit'])
            review = next(task for task in self.tasks.values() if task['mode'] == 'review')
            review_path = Path(review['result']['worktree'])
            self.assertEqual(git(review_path, 'rev-parse', 'HEAD'), done['outputs']['work']['result_commit'])
            self.assertTrue((review_path / 'change-2.txt').is_file())
            self.assertEqual(done['fixes'], 1)
            self.assertEqual(done['publication']['pull_request'], 'https://github.com/o/r/pull/7')
            self.assertEqual([c[0] for c in calls], ['pr', 'comment', 'pr', 'comment'])
            self.assertEqual(calls[2][2], 'https://github.com/o/r/pull/7')  # The fix updates the same PR.
            branch = done['publication']['branch']
            self.assertEqual(git(self.repo, 'rev-parse', branch), done['outputs']['work']['result_commit'])
            self.assertEqual(git(self.repo, 'branch', '--show-current'), 'main')
            self.assertEqual(git(self.repo, 'rev-parse', 'main'), self.base)
            self.assertFalse((self.repo / 'change-1.txt').exists())
            self.assertNotIn(('merge',), calls)
            self.admin('team_merge', {'team_id': room['id']})
            await self.runner.step(room['id'])
            self.assertEqual(get(self.store, room['id'])['publication']['merge'], 'merged')
            self.assertIn(('merge',), calls)
            merge.assert_called_once_with(self.repo, done['publication']['pull_request'], done['outputs']['work']['result_commit'])

    async def test_push_failures_keep_one_branch_across_fix_rounds(self):
        self.reviews = ['VERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        branches = []
        def offline(repo, branch, *args):
            branches.append(branch)
            raise BridgeError('publish_failed', 'GitHub is offline')
        with patch('aabridge_workspace.publish.pull_request', side_effect=offline):
            room = self.start(approval='auto', placement={'mode': 'branch', 'pull_request': True})
            done = await self.run_until(room['id'])
        self.assertEqual(len(branches), 2)
        self.assertEqual(len(set(branches)), 1)
        self.assertEqual(done['publication']['branch'], branches[0])
        self.assertIn('GitHub is offline', done['publication']['error'])
        self.assertEqual(git(self.repo, 'rev-parse', branches[0]), done['outputs']['work']['result_commit'])

    async def test_review_post_failure_is_visible_in_chat_and_saved_state(self):
        self.reviews = ['VERDICT: APPROVED']
        with patch('aabridge_workspace.publish.pull_request', return_value={'url': 'https://github.com/o/r/pull/7'}), \
             patch('aabridge_workspace.publish.review', side_effect=BridgeError('publish_failed', 'GitHub rejected the comment')):
            room = self.start(approval='auto', placement={'mode': 'branch', 'pull_request': True})
            done = await self.run_until(room['id'])
        self.assertIn('GitHub rejected the comment', done['publication']['review_errors']['https://github.com/o/r/pull/7'])
        self.assertTrue(any('could not be posted' in m['text'] for m in done['messages']))

    def test_github_ssh_origin_uses_the_authenticated_https_transport(self):
        import subprocess
        git(self.repo, 'remote', 'add', 'origin', 'git@github.com:owner/project.git')
        resolved = []
        def inspect_push(command, cwd, failure, *args, **kwargs):
            if command[0] == '/usr/bin/git':
                # Ask Git itself which URL these push options select, without
                # connecting. gh's credential helper cannot authenticate SSH.
                index = command.index('push')
                result = subprocess.run(command[:index] + ['remote', 'get-url', '--push', 'origin'], cwd=cwd, capture_output=True, text=True, check=True)
                resolved.append(result.stdout.strip())
            return ''
        git(self.repo, 'branch', 'claude/feature')
        with patch('aabridge_workspace.publish.default_branch', return_value='main'), patch('aabridge_workspace.publish._run', side_effect=inspect_push):
            publish.pull_request(self.repo, 'claude/feature', 'Title', 'Body', 'https://github.com/owner/project/pull/1')
        self.assertEqual(resolved, ['https://github.com/owner/project.git'])

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

    async def test_linked_project_changes_are_reviewed_delivered_and_merged_together(self):
        android = self.root / 'android'
        android.mkdir()
        (android / 'Api.kt').write_text('v1\n')
        git(android, 'init', '--initial-branch=main', '--template='); git(android, 'add', '--all'); git(android, 'commit', '-m', 'Base')
        android_base = git(android, 'rev-parse', 'HEAD')
        original = self.call
        async def with_link(op, body, target, key=None):
            task = await original(op, body, target, key)
            if task.get('mode') == 'work' and op in {'submit', 'revise'}:
                clone = self.state / 'workspaces' / (task['id'] + '.linked') / 'android'
                if not clone.exists():
                    clone.parent.mkdir(parents=True, exist_ok=True)
                    git(clone.parent, 'clone', '--no-local', '--template=', str(android), str(clone))
                (clone / 'Api.kt').write_text(f'v{task["revision"] + 1}\n')
                git(clone, 'add', '--all'); git(clone, 'commit', '-q', '-m', 'Android change')
                head = git(clone, 'rev-parse', 'HEAD')
                task['result']['linked'] = [{'name': 'android', 'access': 'edit', 'repository': str(android), 'worktree': str(clone), 'branch': 'x',
                                             'base_commit': android_base, 'result_commit': head, 'changed_paths': ['Api.kt'],
                                             'diff': git(clone, 'diff', android_base, head), 'diff_truncated': False}]
            return task
        self.runner.call = with_link
        self.reviews = ['VERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        opened, merged = [], []
        def pull(repo, branch, title, body, existing=None):
            opened.append((Path(repo).name, existing))
            return {'url': 'https://github.com/o/' + Path(repo).name + '/pull/1', 'base': 'main'}
        with patch('aabridge_workspace.publish.pull_request', side_effect=pull), patch('aabridge_workspace.publish.review', return_value='review'), \
             patch('aabridge_workspace.publish.merge', side_effect=lambda repo, url, commit: merged.append(url)):
            room = self.start(approval='auto', placement={'mode': 'branch', 'pull_request': True})
            done = await self.run_until(room['id'])
            review = [b for op, b, _, _ in self.requests if op == 'submit' and b['mode'] == 'review'][0]
            self.assertIn('Linked project android (diff evidence only', '\n'.join(review['context']))
            entry = done['publication']['linked'][0]
            self.assertEqual(entry['branch'], done['publication']['branch'])  # Same branch name in every repository.
            self.assertEqual(git(android, 'rev-parse', 'refs/heads/' + entry['branch']), git(android, 'rev-parse', entry['branch']))
            self.assertEqual(git(android, 'rev-parse', 'main'), android_base)
            self.assertEqual(set(opened), {('android', None), ('android', 'https://github.com/o/android/pull/1'),
                                           ('source', None), ('source', 'https://github.com/o/source/pull/1')})
            self.assertEqual(len(opened), 4)  # Each fix round updates the same two pull requests.
            self.assertIn('https://github.com/o/android/pull/1', done['messages'][-1]['text'])
            self.admin('team_merge', {'team_id': room['id']})
            await self.runner.step(room['id'])
            self.assertEqual(sorted(merged), ['https://github.com/o/android/pull/1', 'https://github.com/o/source/pull/1'])
            self.assertEqual(get(self.store, room['id'])['publication']['merge'], 'merged')

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
        self.assertIn('saved connection settings', blocked['error'])

    async def test_direct_connection_failure_does_not_mention_remote_login(self):
        async def offline(op, body, target, key=None):
            raise BridgeError('direct_unavailable', 'Direct connection failed.')
        self.runner.call = offline
        room = self.start(approval='auto')
        await self.runner.step(room['id'])
        error = get(self.store, room['id'])['error']
        self.assertIn('over its direct connection', error)
        self.assertNotIn('Remote Login', error)

    async def test_review_disagreement_reaches_the_admin_without_spending_the_fix_rounds(self):
        self.assertEqual(verdict('Open question: metric or imperial.\nVERDICT: NEEDS_ADMIN'), 'admin')
        # The worker disputes the review and changes nothing; the leader repeats itself.
        self.reviews = ['1. Rename it\nVERDICT: CHANGES_REQUESTED'] * 2
        room = self.start(approval='auto', max_fixes=5)
        for _ in range(20):
            await self.runner.step(room['id'])
            current = get(self.store, room['id'])
            if current['stage'] == 'work' and current.get('work_reason') == 'review':
                self.idle_work = True
            if current['status'] == 'awaiting_admin':
                break
        self.assertEqual((current['gate']['kind'], current['gate']['cause'], current['fixes']), ('review', 'dispute', 1))
        self.assertEqual([mode for _, mode in self.stages()], ['plan', 'work', 'review', 'work', 'review'])
        rework = next(body['text'] for op, body, _, _ in self.requests if op == 'revise' and 'requested changes' in body.get('text', ''))
        self.assertIn('FIXED', rework)
        self.assertIn('DISPUTED', rework)
        rereview = [body['text'] for op, body, _, _ in self.requests if op == 'revise' and 'This is a re-review' in body.get('text', '')]
        self.assertEqual(len(rereview), 1)
        # The admin accepts the work; the task ends without an approval.
        done = self.admin('team_decide', {'team_id': room['id'], 'decision': 'finish'})
        self.assertEqual((done['status'], done['verdict']), ('completed', 'changes'))

    async def test_leader_can_hand_an_open_review_point_to_the_admin(self):
        self.reviews = ['Kilograms or pounds is a product choice.\nVERDICT: NEEDS_ADMIN']
        room = self.start(approval='auto', max_fixes=5)
        waiting = await self.run_until(room['id'])
        self.assertEqual((waiting['status'], waiting['gate']['cause'], waiting['fixes']), ('awaiting_admin', 'leader', 0))
        self.assertEqual([mode for _, mode in self.stages()], ['plan', 'work', 'review'])
        self.reviews = ['VERDICT: APPROVED']
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'continue', 'text': 'Use kilograms.'})
        self.assertEqual((await self.run_until(room['id']))['verdict'], 'approved')

    async def test_follow_up_question_is_answered_without_a_build_and_keeps_the_result(self):
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto')
        first = self.store.handle(request('team_status', {'team_id': room['id']}), 'local', {})
        done = await self.run_until(room['id'])
        self.assertEqual(done['status'], 'completed')
        commit = done['outputs']['work']['result_commit']
        self.plans = ['It is approved; nothing is open.\nNEXT: ANSWER_ONLY']
        self.admin('team_reply', {'team_id': room['id'], 'text': 'Why is the PR not approved?'})
        answered = await self.run_until(room['id'])
        self.assertEqual([mode for _, mode in self.stages()], ['plan', 'work', 'review', 'plan'])
        shown = self.store.handle(request('team_status', {'team_id': room['id']}), 'local', {})
        self.assertEqual((shown['status'], shown['answered'], shown['verdict'], shown['result']['result_commit']), ('completed', True, 'approved', commit))
        self.assertEqual((shown['title'], shown['objective']), ('Fix weight validation', 'Why is the PR not approved?'))
        self.assertTrue(first['title'])
        listed = self.store.handle(request('teams', {}), 'local', {})['teams'][0]
        self.assertEqual((listed['title'], listed['answered']), ('Fix weight validation', True))
        # The next change request still continues the saved implementation.
        self.reviews = ['VERDICT: APPROVED']
        self.admin('team_reply', {'team_id': room['id'], 'text': 'Now add pounds.'})
        again = await self.run_until(room['id'])
        self.assertEqual([op for op, mode in self.stages() if mode == 'work'], ['submit', 'revise'])
        self.assertFalse(again.get('answered'))

    async def test_progress_rows_are_not_saved_and_old_ones_are_hidden_not_deleted(self):
        room = self.start(approval='auto')
        saved = get(self.store, room['id'])
        from aabridge_core.teams import message, save
        message(saved, 'AABridge', 'Leader is planning…')
        save(self.store, saved)
        self.reviews = ['VERDICT: APPROVED']
        done = await self.run_until(room['id'])
        texts = [m['text'] for m in done['messages']]
        self.assertEqual(texts.count('Leader is planning…'), 1)  # The old saved row is still in the database.
        self.assertFalse({'Follower is implementing…', 'Leader is reviewing…'} & set(texts))
        shown = self.store.handle(request('team_status', {'team_id': room['id']}), 'local', {})
        self.assertNotIn('Leader is planning…', [m['text'] for m in shown['messages']])
        self.assertEqual(shown['earlier_messages'], 0)
        self.assertTrue(shown['stage_started'])

    async def test_worker_question_is_answered_in_place_and_the_same_turn_continues(self):
        self.asks, self.reviews = 'work', ['VERDICT: APPROVED']
        room = self.start(approval='auto')
        waiting = await self.run_until(room['id'])
        self.assertEqual((waiting['status'], waiting['stage'], waiting['gate']['kind'], waiting['gate']['reason']), ('awaiting_admin', 'work', 'question', 'Kilograms or pounds?'))
        self.assertIn(('claude · follower', 'Kilograms or pounds?'), [(m['speaker'], m['text']) for m in waiting['messages']])
        for decision, text in (('finish', None), ('continue', None), ('replan', 'Other plan')):
            with self.assertRaises(BridgeError):
                self.admin('team_decide', {'team_id': room['id'], 'decision': decision, **({'text': text} if text else {})})
        self.admin('team_decide', {'team_id': room['id'], 'decision': 'continue', 'text': 'Kilograms.'})
        done = await self.run_until(room['id'])
        self.assertEqual((done['status'], done['verdict']), ('completed', 'approved'))
        # One build task: the question did not restart it, and the answer was not also sent as a note.
        self.assertEqual([op for op, body, _, _ in self.requests if op in {'submit', 'revise', 'answer'}], ['submit', 'submit', 'answer', 'submit'])
        self.assertEqual(next(t for t in self.tasks.values() if t['mode'] == 'work')['answer'], 'Kilograms.')
        self.assertEqual(done['guidance'], [])

    def test_earlier_messages_can_be_paged_back(self):
        room = self.start(approval='auto')
        saved = get(self.store, room['id'])
        from aabridge_core.teams import message, save
        for number in range(200):
            message(saved, 'AABridge', 'Note ' + str(number))
        save(self.store, saved)
        status = lambda **extra: self.store.handle(request('team_status', {'team_id': room['id'], **extra}), 'local', {})
        latest = status()
        self.assertEqual((len(latest['messages']), latest['earlier_messages'], latest['messages'][-1]['text']), (120, 82, 'Note 199'))
        page = status(before=latest['earlier_messages'])
        self.assertEqual((len(page['messages']), page['earlier_messages'], page['messages'][-1]['text']), (60, 22, 'Note 79'))
        first = status(before=page['earlier_messages'])
        self.assertEqual((len(first['messages']), first['earlier_messages'], first['messages'][0]['text']), (22, 0, 'Add a weight input screen'))
        with self.assertRaises(BridgeError):
            validate_request({'version': 1, 'op': 'team_status', 'key': 'k1', 'body': {'team_id': 'team-1', 'before': 0}})

    async def test_large_change_is_sent_to_the_leader_in_parts_and_reviewed(self):
        self.big, self.reviews = True, ['1. Fix it\nVERDICT: CHANGES_REQUESTED', 'VERDICT: APPROVED']
        room = self.start(approval='auto')
        blocked = await self.run_until(room['id'])  # An older leader cannot take the upload.
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('Update AABridge on the leader Mac', blocked['error'])
        self.assertEqual([mode for _, mode in self.stages()], ['plan', 'work'])
        self.project['capabilities']['review_snapshot_parts'] = True
        self.admin('team_retry', {'team_id': room['id']})
        done = await self.run_until(room['id'])
        self.assertEqual((done['status'], done['verdict']), ('completed', 'approved'))
        self.assertEqual(len(self.uploaded), 2)  # First review, then the re-review of the fix.
        self.assertTrue(all(size > 60000 for size in self.uploaded))
        self.assertEqual(list((self.state / 'outbox').iterdir()), [])
        self.assertNotIn('snapshot_upload', done)
        review = next(t for t in self.tasks.values() if t['mode'] == 'review')
        self.assertTrue((Path(review['result']['worktree']) / 'asset-2.bin').is_file())

    async def test_unfinished_work_is_visible_on_a_live_branch_without_touching_the_worker(self):
        self.reviews = ['VERDICT: APPROVED']
        room = self.start(approval='auto', placement={'mode': 'branch'})
        for _ in range(10):
            await self.runner.step(room['id'])
            if get(self.store, room['id'])['stage'] == 'work':
                break
        self.pending = True  # The worker's turn is still running.
        await self.runner.step(room['id'])
        work = next(t for t in self.tasks.values() if t['mode'] == 'work')
        path = Path(work['result']['worktree'])
        (path / 'half-done.txt').write_text('not committed yet')
        before = (git(path, 'rev-parse', 'HEAD'), git(path, 'status', '--porcelain'), git(path, 'diff', '--cached', '--name-only'))
        saved = get(self.store, room['id']); saved['preview_checked'] = 0
        from aabridge_core.teams import save
        save(self.store, saved)
        await self.runner.step(room['id'])
        shown = self.store.handle(request('team_status', {'team_id': room['id']}), 'local', {})['preview']
        self.assertEqual(shown['branch'], 'aabridge-live/' + room['id'][-6:])
        self.assertEqual(git(self.repo, 'show', shown['branch'] + ':half-done.txt'), 'not committed yet')
        self.assertEqual(git(self.repo, 'branch', '--show-current'), 'main')
        # The worker's own checkout, index and branch are exactly as it left them.
        self.assertEqual((git(path, 'rev-parse', 'HEAD'), git(path, 'status', '--porcelain'), git(path, 'diff', '--cached', '--name-only')), before)
        (path / 'half-done.txt').unlink()
        work['status'] = 'completed'; self.pending = False
        done = await self.run_until(room['id'])
        self.assertEqual(done['status'], 'completed')
        self.assertNotIn('preview', done)
        self.assertNotIn('aabridge-live/', git(self.repo, 'branch', '--list'))
        self.assertTrue(done['publication']['branch'].startswith('claude/'))

    def test_leader_review_is_posted_as_a_github_review_and_falls_back_when_refused(self):
        summary = '1. Negative weights pass.\nINLINE: app/save.py:12: reject values below zero\nINLINE: other/file.py:3: not part of this change\nVERDICT: CHANGES_REQUESTED'
        comments = publish.inline_comments(summary, ['app/save.py'])
        self.assertEqual(comments, [{'path': 'app/save.py', 'line': 12, 'side': 'RIGHT', 'body': 'reject values below zero'}])
        url, sent = 'https://github.com/o/r/pull/7', []
        def gh(refuse):
            def run(repo, *arguments, stdin=None, failure=''):
                if arguments[0] == 'api':
                    import json
                    payload = json.loads(stdin)
                    sent.append((arguments[3], payload['event'], len(payload.get('comments', [])), '- `app/save.py:12` reject' in payload['body']))
                    if (payload['event'], bool(payload.get('comments'))) in refuse:
                        raise BridgeError('publish_failed', failure)
                else:
                    sent.append((arguments[0], arguments[1]))
                return ''
            return run
        with patch('aabridge_workspace.publish._gh', side_effect=gh(set())):
            self.assertEqual(publish.review(self.repo, url, summary, 'changes', 'a' * 40, comments), 'change request with inline comments')
        self.assertEqual(sent, [('repos/o/r/pulls/7/reviews', 'REQUEST_CHANGES', 1, False)])
        sent.clear()  # The author of a pull request may only comment on it, and the line is outside the diff.
        with patch('aabridge_workspace.publish._gh', side_effect=gh({('REQUEST_CHANGES', True), ('COMMENT', True)})):
            self.assertEqual(publish.review(self.repo, url, summary, 'changes', 'a' * 40, comments), 'review')
        self.assertEqual([entry[1:] for entry in sent], [('REQUEST_CHANGES', 1, False), ('COMMENT', 1, False), ('COMMENT', 0, True)])
        sent.clear()  # Nothing accepted as a review: the plain comment still carries it.
        with patch('aabridge_workspace.publish._gh', side_effect=gh({('APPROVE', False), ('COMMENT', False)})):
            self.assertEqual(publish.review(self.repo, url, 'Fine.', 'approved', 'a' * 40, []), 'comment')
        self.assertEqual(sent[-1], ('pr', 'comment'))


if __name__ == '__main__':
    unittest.main()
