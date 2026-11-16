from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from aabridge_cli.actions import request
from aabridge_core import teams
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from record_team_run import record


class RunRecordTests(unittest.TestCase):
    """The evidence file must not let a one-Mac or unfinished run pass as a two-Mac check."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-record-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name).resolve() / 'state'
        initialize(self.state)
        config = load_config(self.state)
        config['remotes']['mini'] = {'label': 'Mac mini', 'host': 'Mac-mini.local', 'transport': 'tls'}
        save_config(self.state, config)

    def run_task(self, key, follower, **changes):
        store = Store(self.state)
        body = {'objective': 'Add a weight input screen', 'approval': 'auto',
                'leader': {'target': 'local', 'agent': 'codex', 'project': 'app'}, 'follower': {'target': follower, 'agent': 'claude', 'project': 'app'}}
        room = teams.get(store, store.handle(request('team_start', body, key), 'local', {})['id'])
        room.update(changes)
        teams.message(room, 'codex · leader', 'Looks right.\nVERDICT: APPROVED')
        teams.save(store, room)
        store.close()
        return room['id']

    def test_only_a_finished_run_across_two_macs_is_reported_as_one(self):
        turns = [{'target': 'local', 'task_id': 'task-1', 'stage': 'plan'}, {'target': 'mini', 'task_id': 'task-2', 'stage': 'work'}, {'target': 'local', 'task_id': 'task-3', 'stage': 'review'}]
        two = self.run_task('two', 'mini', status='completed', verdict='approved', stage_tasks=turns,
                            usage={'follower': {'mini/task-2/4': {'input': 900, 'output': 100, 'cached': 0}}})
        text = record(self.state, two, ping=False)
        self.assertIn('**Two Macs:** yes, this Mac led and Mac mini built.', text)
        self.assertIn('**Full cycle:** yes', text)
        self.assertIn('**Counts as a release check:** no.', text)  # Versions were not checked.
        self.assertIn('Worker: 900 in, 100 out, 0 cached', text)
        self.assertIn('- work on Mac mini (`task-2`)', text)
        self.assertIn('**codex · leader**: Looks right. VERDICT: APPROVED', text)
        one = self.run_task('one', 'local', status='running', stage='work', stage_tasks=turns[:1])
        text = record(self.state, None, ping=False)  # The latest task.
        self.assertIn('`' + one + '`', text)
        self.assertIn('so this is not a two-Mac run', text)
        self.assertIn('**Full cycle:** no, the task is `running` at stage `work`.', text)
        self.assertIn('**Counts as a release check:** no.', text)


if __name__ == '__main__':
    unittest.main()
