from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aabridge_adapter_codex.native import NativeWorker
from aabridge_core.config import initialize
from aabridge_protocol.messages import BridgeError


class NativeCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_cli_formats_use_checkout_and_saved_session_without_model_calls(self):
        for agent in ('codex', 'claude'):
            with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-native-') as tmp:
                state = Path(tmp).resolve() / 'state'; initialize(state)
                workspace = state / 'workspaces/task-native'; workspace.mkdir()
                executable = state / 'fixture-agent'
                executable.write_text('#!' + sys.executable + '\n' + '''
import json,sys
from pathlib import Path
if '--version' in sys.argv:
    print('Fixture agent 1.0'); raise SystemExit(0)
prompt=sys.stdin.read()
Path('received-prompt.txt').write_text(prompt)
Path('received-args.json').write_text(json.dumps(sys.argv[1:]))
if 'Review validation is authorized' in prompt:
    import os,tempfile
    with tempfile.TemporaryDirectory() as scratch:
        Path(scratch, 'validation-output.txt').write_text('check output')
        assert scratch.startswith(os.environ['TMPDIR'])
    Path(os.environ['GOCACHE'], 'fixture-cache').write_text('cached check')
agent=AGENT
if agent=='codex':
    print(json.dumps({'type':'thread.started','thread_id':'fixture-session'}))
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Changed files and ran the fixture check.'}}))
    print(json.dumps({'type':'turn.completed'}))
else:
    print(json.dumps({'type':'system','subtype':'init','session_id':'fixture-session'}))
    print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'Changed files and ran the fixture check.'}))
'''.replace('AGENT', repr(agent)))
                executable.chmod(0o700)
                project = {'adapter': agent, agent + '_executable': str(executable), agent + '_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}
                task = {'id':'task-native','objective':'Build an app','context':[],'pending':None,'thread_id':None}
                sessions = []; pids = []
                worker = NativeWorker(state, task, project, lambda *s: sessions.append(s), pids.append)
                await worker.start(); await worker.close()
                output = await worker.turn({}); await worker.close()
                self.assertIn('Changed files', output['summary'])
                self.assertEqual(sessions, [('fixture-session', 'fixture-session')])
                task['thread_id'] = 'fixture-session'
                task['pending'] = {'text': 'Improve it'}
                await worker.turn({}); await worker.close()
                args = json.loads((workspace / 'received-args.json').read_text())
                self.assertIn('fixture-session', args)
                self.assertIn('resume' if agent == 'codex' else '--resume', args)
                self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
                self.assertNotIn('--dangerously-skip-permissions', args)
                self.assertIn('Improve it', (workspace / 'received-prompt.txt').read_text())
                self.assertIsNone(pids[-1])
                task['mode'] = 'plan'
                args = worker.arguments()
                self.assertIn('sandbox_mode="read-only"' if agent == 'codex' else 'Read,Glob,Grep', args)
                task.update(mode='review', review_snapshot={'commit': 'a' * 40, 'bundle': ''}, base_commit='b' * 40)
                args = worker.arguments()
                self.assertIn('sandbox_mode="workspace-write"' if agent == 'codex' else 'Read,Glob,Grep,Bash', args)
                if agent == 'codex':
                    self.assertTrue(any(str(state / 'review-runtime/task-native') in arg for arg in args))
                await worker.turn({}); await worker.close()
                self.assertTrue((state / 'review-runtime/task-native/go-build/fixture-cache').is_file())
                self.assertIn('Review validation is authorized', (workspace / 'received-prompt.txt').read_text())
                executable.write_text('changed')
                with self.assertRaises(BridgeError):
                    worker.executable()
