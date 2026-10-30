from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_adapter_codex.native import NativeWorker
from aabridge_cli.actions import configure_coding_project, request
from aabridge_core.config import initialize, load_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError
from aabridge_workspace import linked
from aabridge_workspace.git import Workspace, git


def repository(path: Path, name: str) -> Path:
    path.mkdir()
    (path / 'README.md').write_text(name + '\n')
    git(path, 'init', '--initial-branch=main', '--template=')
    git(path, 'add', '--all'); git(path, 'commit', '-m', 'Base')
    return path


class LinkedProjectTests(unittest.TestCase):
    """A server project that can edit its Android app and read its Mac app."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-linked-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.state = root / 'state'
        initialize(self.state)
        self.server = repository(root / 'server', 'server')
        self.android = repository(root / 'Sparshur Android', 'android')
        self.mac = repository(root / 'mac-app', 'mac')
        self.links = [{'repository': str(self.android), 'access': 'edit'}, {'repository': str(self.mac), 'access': 'read'}]

    def configure(self, agent='codex', links=None):
        with patch('aabridge_cli.actions.runtime_inventory', return_value={agent: {'status': 'found', 'executable': sys.executable}}):
            configure_coding_project(self.state, 'server', self.server, [], agent, self.links if links is None else links)
        return load_config(self.state)

    def submit(self, config, mode='work'):
        project = config['projects']['server']
        body = {'project_id': 'server', 'recipient': project['worker'], 'base_commit': project['base_commit'], 'objective': 'Sync the apps',
                'scope': ['*'], 'context': [], 'acceptance': ['Review'], 'limits': project['limits'], 'permission_profile': 'coding-v1', 'mode': mode}
        store = Store(self.state)
        self.addCleanup(store.close)
        return store.handle(request('submit', body), 'local', config), project

    def test_links_are_validated_and_named_after_their_folders(self):
        project = self.configure()['projects']['server']
        self.assertEqual([(l['name'], l['access']) for l in project['links']], [('sparshur-android', 'edit'), ('mac-app', 'read')])
        for bad in ([{'repository': str(self.server), 'access': 'edit'}],
                    [{'repository': str(self.android), 'access': 'edit'}, {'repository': str(self.android), 'access': 'read'}],
                    [{'repository': str(self.android), 'access': 'admin'}],
                    [{'repository': str(self.state), 'access': 'read'}]):
            with self.assertRaises(BridgeError):
                self.configure(links=bad)
        plain = self.state.parent / 'notes'
        plain.mkdir()
        with self.assertRaisesRegex(BridgeError, 'isn’t a Git project'):
            self.configure(links=[{'repository': str(plain), 'access': 'read'}])

    def test_task_edits_linked_project_and_read_only_changes_are_discarded(self):
        task, project = self.submit(self.configure())
        work = Workspace(self.state, task, project)
        work.prepare()
        (android, android_path), (mac, mac_path) = linked.paths(self.state, task, project)
        self.assertEqual(git(android_path, 'branch', '--show-current'), work.branch)
        self.assertEqual(git(android_path, 'remote'), '')
        (work.path / 'api.go').write_text('package api\n')
        (android_path / 'Api.kt').write_text('class Api\n')
        (mac_path / 'README.md').write_text('changed\n')
        result = work.result({'summary': 'Changed the API and its Android client', 'issues': []})
        edit, read = result['linked']
        self.assertEqual((edit['name'], edit['changed_paths']), ('sparshur-android', ['Api.kt']))
        self.assertIn('+class Api', edit['diff'])
        self.assertEqual((read['changed_paths'], read['diff']), ([], ''))
        self.assertEqual((mac_path / 'README.md').read_text(), 'mac\n')
        self.assertIn('Changes to mac-app were discarded', ' '.join(result['limitations']))
        # The admin's own folders are untouched.
        self.assertFalse((self.android / 'Api.kt').exists())
        self.assertEqual(git(self.android, 'rev-parse', 'HEAD'), edit['base_commit'])

    def test_review_steps_never_keep_linked_edits(self):
        task, project = self.submit(self.configure(), mode='review')
        work = Workspace(self.state, task, project)
        work.prepare()
        (_, android_path), _ = linked.paths(self.state, task, project)
        (android_path / 'Api.kt').write_text('class Api\n')
        result = work.result({'summary': 'Reviewed', 'issues': []})
        self.assertEqual(result['linked'][0]['changed_paths'], [])
        self.assertFalse((android_path / 'Api.kt').exists())

    def test_agents_get_the_linked_folders_and_a_map_of_them(self):
        for agent in ('codex', 'claude'):
            task, project = self.submit(self.configure(agent))
            Workspace(self.state, task, project).prepare()
            arguments = NativeWorker(self.state, task, project, lambda *a: None, lambda p: None).arguments()
            (_, android_path), (_, mac_path) = linked.paths(self.state, task, project)
            if agent == 'codex':
                roots = next(a for a in arguments if a.startswith('sandbox_workspace_write.writable_roots='))
                self.assertEqual(json.loads(roots.split('=', 1)[1]), [str(android_path)])  # Read links stay read-only.
            else:
                self.assertIn(str(android_path), arguments)
                self.assertIn(str(mac_path), arguments)
            description = linked.describe(self.state, task, project)
            self.assertIn('sparshur-android): you may edit it', description)
            self.assertIn('mac-app): read only', description)
            store = Store(self.state)
            store.update(task['id'], 'cancelled', status='cancelled')  # Free the project for the next agent.
            store.close()

    def test_peers_see_link_names_and_access_but_not_local_paths(self):
        config = self.configure()
        store = Store(self.state)
        self.addCleanup(store.close)
        projects = store.handle(request('projects', {}), 'local', config)
        self.assertTrue(projects['server']['capabilities']['shared_task_history'])
        self.assertEqual(projects['server']['links'], [{'name': 'sparshur-android', 'access': 'edit'}, {'name': 'mac-app', 'access': 'read'}])
        self.assertNotIn(str(self.android), json.dumps(projects))


if __name__ == '__main__':
    unittest.main()
