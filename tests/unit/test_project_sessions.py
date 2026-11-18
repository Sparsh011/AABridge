"""Projects as one folder with several agents, and sessions visible on every member Mac."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import request, save_project_agents
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_core.teams import NEXT_RULE, TeamRunner
from aabridge_protocol.messages import BridgeError
from aabridge_protocol.titles import project_name
from aabridge_workspace.git import git


class ProjectAgentsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-projects-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.state, self.repo = root / 'state', root / 'Sparshur-Service'
        initialize(self.state)
        self.repo.mkdir()
        (self.repo / 'README.md').write_text('Base\n')
        git(self.repo, 'init', '--initial-branch=main', '--template='); git(self.repo, 'add', '--all'); git(self.repo, 'commit', '-m', 'Base')

    def save(self, agents, found=('codex', 'claude'), **extra):
        inventory = {agent: {'status': 'found', 'executable': sys.executable} if agent in found else {'status': 'unavailable', 'message': f'{agent} was not found.'}
                     for agent in ('codex', 'claude')}

        async def enable(state, project_id):
            config = load_config(state)
            config['projects'][project_id]['execution_enabled'] = True
            save_config(state, config)
        with patch('aabridge_cli.actions.runtime_inventory', return_value=inventory), patch('aabridge_cli.actions.enable_project', side_effect=enable):
            return save_project_agents(self.state, self.repo, agents, [], **extra)

    def test_one_folder_gets_every_agent_and_a_missing_agent_does_not_block_the_other(self):
        saved = self.save(['codex', 'claude'], found=('claude',))
        self.assertEqual(saved['saved'], ['project-sparshur-service-claude'])
        self.assertIn('codex was not found', saved['agent_errors']['codex'])
        # Adding Codex later reuses the folder's name; Claude keeps its ID and settings.
        saved = self.save(['codex', 'claude'])
        self.assertEqual(sorted(saved['saved']), ['project-sparshur-service-claude', 'project-sparshur-service-codex'])
        projects = load_config(self.state)['projects']
        self.assertEqual({p['adapter'] for p in projects.values()}, {'codex', 'claude'})
        self.assertEqual({p['name'] for p in projects.values()}, {'Sparshur-Service'})
        self.assertTrue(all(p['execution_enabled'] for p in projects.values()))

    def test_an_id_taken_by_another_folder_is_not_overwritten(self):
        config = load_config(self.state)
        config['projects']['project-sparshur-service-codex'] = {'repository': '/elsewhere', 'adapter': 'codex'}
        save_config(self.state, config)
        saved = self.save(['codex'])
        self.assertEqual(saved['saved'], [])
        self.assertIn('already belongs', saved['agent_errors']['codex'])
        self.assertEqual(load_config(self.state)['projects']['project-sparshur-service-codex']['repository'], '/elsewhere')

    def test_older_projects_are_matched_by_the_folder_in_their_id(self):
        self.assertEqual(project_name('project-sparshur-service-claude'), 'sparshur-service')
        self.assertEqual(project_name('project-sparshur-service-codex'), 'sparshur-service')
        self.assertEqual(project_name('demo'), 'demo')
        self.assertEqual(project_name('anything', {'name': 'Sparshur-Service'}), 'Sparshur-Service')


class SharedSessionTests(unittest.TestCase):
    """The admin Mac runs a session; the other member Mac reads it, and nothing else."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-sessions-')
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name).resolve() / 'state'
        initialize(self.state)
        config = load_config(self.state)
        for peer in ('mini', 'other'):
            config['peers'][peer] = {'enabled': True, 'projects': []}
        save_config(self.state, config)
        self.config = load_config(self.state)
        self.store = Store(self.state)
        self.addCleanup(self.store.close)
        body = {'objective': 'Retry failed relay deliveries',
                'leader': {'target': 'local', 'agent': 'codex', 'project': 'project-app-codex'},
                'follower': {'target': 'mini', 'agent': 'claude', 'project': 'project-app-claude'},
                'placement': {'mode': 'branch', 'pull_request': True}}
        self.room = self.store.handle(request('team_start', body, 'start-shared'), 'local', self.config)
        self.store.handle(request('team_start', dict(body, follower={**body['follower'], 'target': 'local'}), 'start-private'), 'local', self.config)

    def test_member_mac_sees_its_sessions_from_its_own_side_without_admin_paths(self):
        listed = self.store.handle(request('teams', {}), 'mini', self.config)['teams']
        self.assertEqual([room['id'] for room in listed], [self.room['id']])
        self.assertEqual(listed[0]['follower']['target'], 'local')
        self.assertEqual(listed[0]['leader']['target'], self.config['node_id'])
        self.assertTrue(listed[0]['read_only'])
        status = self.store.handle(request('team_status', {'team_id': self.room['id']}), 'mini', self.config)
        self.assertEqual(status['messages'][0]['text'], 'Retry failed relay deliveries')
        self.assertEqual(status['host'], self.config['node_id'])
        self.assertEqual(self.store.handle(request('team_activity', {'team_id': self.room['id'], 'cursor': 0}), 'mini', self.config)['entries'], [])

    def test_other_macs_and_steering_are_refused(self):
        self.assertEqual(self.store.handle(request('teams', {}), 'other', self.config)['teams'], [])
        with self.assertRaises(BridgeError) as error:
            self.store.handle(request('team_status', {'team_id': self.room['id']}), 'other', self.config)
        self.assertEqual(error.exception.code, 'not_found')
        for op, body in (('team_cancel', {'team_id': self.room['id']}), ('team_guide', {'team_id': self.room['id'], 'text': 'Faster'})):
            with self.assertRaises(BridgeError) as error:
                self.store.handle(request(op, body, 'peer-' + op), 'mini', self.config)
            self.assertEqual(error.exception.code, 'permission_denied')
        self.config['peers']['mini']['enabled'] = False
        with self.assertRaises(BridgeError):
            self.store.handle(request('teams', {}), 'mini', self.config)


class LeaderSeesWorkerLinksTests(unittest.TestCase):
    """A leader whose own links are read-only planned no client changes and answered instead of building."""

    def plan(self, follower_links):
        temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-links-')
        self.addCleanup(temp.cleanup)
        state = Path(temp.name).resolve() / 'state'
        initialize(state)
        store = Store(state)
        self.addCleanup(store.close)
        room = {'stage': 'plan', 'objective': 'Implement the next steps', 'approval': 'auto', 'messages': [], 'guidance': [],
                'leader': {'agent': 'claude', 'target': 'local', 'project': 'app'}, 'follower': {'agent': 'codex', 'target': 'mbp', 'project': 'app'},
                'leader_project': {'worker': 'claude', 'base_commit': 'a' * 40, 'limits': {'runtime_seconds': 3600, 'max_turns': 100, 'max_revisions': 100},
                                   'links': [{'name': 'sparshurandroid', 'access': 'read'}, {'name': 'sparshur-mac-client', 'access': 'read'}]},
                'follower_project': {'links': follower_links}}
        return TeamRunner(state, store).stage_body(room, room['leader_project'])

    def test_plan_names_the_follower_edit_access_and_build_is_the_rule_for_changes(self):
        body = self.plan([{'name': 'sparshurandroid', 'access': 'edit'}, {'name': 'sparshur-mac-client', 'access': 'read'}])
        links = next(item for item in body['context'] if item.startswith('Linked projects in this team'))
        self.assertIn('The follower can edit sparshurandroid in this same task', links)
        self.assertIn('can only read sparshur-mac-client', links)
        self.assertIn('Can edit', links)
        self.assertIn(NEXT_RULE, body['objective'])
        self.assertIn('Never use ANSWER_ONLY when the admin asks for changes', body['objective'])

    def test_no_links_adds_nothing(self):
        self.assertFalse(any(item.startswith('Linked projects in this team') for item in self.plan([])['context']))
