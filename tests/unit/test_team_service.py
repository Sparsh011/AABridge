from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aabridge_cli.actions import request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_daemon.server import Supervisor
from aabridge_transport_local.client import call
from aabridge_workspace.git import git


class TeamServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_supervisors_run_native_team_and_followup_over_direct_tls(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-team-ipc-') as tmp:
            root = Path(tmp).resolve(); repo = root / 'source'; repo.mkdir()
            (repo / 'README.md').write_text('Baseline')
            git(repo, 'init', '--initial-branch=main', '--template='); git(repo, 'add', '--all'); git(repo, 'commit', '-m', 'Baseline')
            base = git(repo, 'rev-parse', 'HEAD')
            executable = root / 'fixture-agent'
            executable.write_text('#!' + sys.executable + '\n' + '''
import json,sys,time
from pathlib import Path
if '--version' in sys.argv:
    print('Fixture version'); raise SystemExit(0)
prompt=sys.stdin.read()
is_claude='-p' in sys.argv
readonly='Inspect and respond only; do not edit files or create commits.' in prompt
if not readonly:
    path=Path('built.txt')
    path.write_text((path.read_text() if path.exists() else '')+'Feature delivered\\n')
summary='Review: ready after checking the supplied diff' if 'Review the follower' in prompt else 'Implement the requested feature' if readonly else 'Implemented the feature in built.txt'
session='session-'+Path.cwd().name
if is_claude:
    print(json.dumps({'type':'system','subtype':'init','session_id':session,'model':'claude-test-resolved'}),flush=True)
    print(json.dumps({'type':'assistant','message':{'id':'progress','content':[{'type':'text','text':'Checking the project before editing.'}]}}),flush=True)
    time.sleep(.8)
    print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':summary}))
else:
    print(json.dumps({'type':'thread.started','thread_id':session}),flush=True)
    print(json.dumps({'type':'item.completed','item':{'id':'progress','type':'agent_message','text':'Planning the implementation.'}}),flush=True)
    time.sleep(.8)
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':summary}}))
    print(json.dumps({'type':'turn.completed'}))
''')
            executable.chmod(0o700)
            nodes = {}
            servers, schedules = [], []
            for name, agent in (('book','codex'),('mini','claude')):
                state = root / name; initialize(state)
                config = load_config(state)
                config['projects']['app'] = {'repository':str(repo),'adapter':agent,'worker':agent,'scope':['*'],
                    'base_commit':base,'verification':{'profile':'repository-v1'},'permission_profile':'coding-v1',
                    'execution_enabled':True,'limits':{'runtime_seconds':60,'max_turns':10,'max_revisions':5},
                    agent+'_executable':str(executable),agent+'_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}
                save_config(state, config)
                supervisor = Supervisor(state)
                server = await asyncio.start_unix_server(supervisor.accept, str(state / 'bridge.sock'))
                (state / 'bridge.sock').chmod(0o600)
                servers.append(server); nodes[name]=(state,supervisor)
            async def operation(op, body, key=None):
                return await asyncio.to_thread(call, nodes['book'][0], request(op, body, key))
            async def completed(room_id):
                for _ in range(200):
                    room = await operation('team_status', {'team_id':room_id})
                    if room['status'] == 'completed': return room
                    self.assertNotIn(room['status'], ('blocked','failed'), room)
                    await asyncio.sleep(.1)
                self.fail('Team did not complete')
            from aabridge_core.direct import identity, certificate_fingerprint
            from aabridge_core.pairing import connect
            from aabridge_daemon.direct import DirectListener
            certificates = {name: await asyncio.to_thread(identity, node[0]) for name, node in nodes.items()}
            listener = DirectListener(nodes['mini'][1])
            port = await listener.start('127.0.0.1', 0)
            for name, other in (('book', 'mini'), ('mini', 'book')):
                pem = certificates[other]
                connect(nodes[name][0], {'version':2, 'transport':'tls', 'id':load_config(nodes[other][0])['node_id'],
                                        'name':other, 'host':'127.0.0.1', 'port':port, 'certificate':pem,
                                        'fingerprint':certificate_fingerprint(pem)})
            state = nodes['book'][0]; config = load_config(state)
            config['remotes']['mini'] = config['remotes'].pop(load_config(nodes['mini'][0])['node_id'])
            save_config(state, config)
            state = nodes['mini'][0]; config = load_config(state)
            config['peers'][load_config(nodes['book'][0])['node_id']]['projects'] = ['app']
            save_config(state, config)
            try:
                schedules = [asyncio.create_task(supervisor.schedule()) for _, supervisor in nodes.values()]
                body = {'objective':'Build a feature','leader':{'target':'local','agent':'codex','project':'app','model_settings':{'model':'codex-test','effort':'high'}},
                        'follower':{'target':'mini','agent':'claude','project':'app','model_settings':{'model':'opus','effort':'medium'}}}
                room = await operation('team_start', body, 'team-ipc-one')
                # Observe the stream while a model turn is still running.
                observed = False
                for _ in range(100):
                    page = await operation('team_activity', {'team_id':room['id'],'cursor':0})
                    status = await operation('team_status', {'team_id':room['id']})
                    if page['entries'] and status['status'] == 'running':
                        observed = True
                        break
                    await asyncio.sleep(.05)
                self.assertTrue(observed, 'Activity should arrive before the final result')
                done = await completed(room['id'])
                page = await operation('team_activity', {'team_id':room['id'],'cursor':0})
                self.assertEqual({e['role'] for e in page['entries']}, {'leader','follower'})
                self.assertIn('Checking the project', json.dumps(page))
                self.assertIn('codex-test', json.dumps(page))
                self.assertIn('claude-test-resolved', json.dumps(page))
                work = Path(done['result']['worktree'])
                self.assertEqual((work / 'built.txt').read_text(), 'Feature delivered\n')
                # The leader must inspect the same Git object and files as
                # the worker, over both IPC and real mutual TLS fixtures.
                room_state = nodes['book'][1].store.db.execute('SELECT document FROM teams WHERE id=?', (room['id'],)).fetchone()
                saved = json.loads(room_state[0])
                leader_task = saved['resume']['review']['task_id']
                leader_state = nodes['book' if saved['leader']['target'] == 'local' else saved['leader']['target']][0]
                review_path = leader_state / 'workspaces' / leader_task
                self.assertEqual(git(review_path, 'rev-parse', 'HEAD'), done['result']['result_commit'])
                self.assertEqual((review_path / 'built.txt').read_text(), 'Feature delivered\n')
                await operation('team_reply', {'team_id':room['id'],'text':'Add another feature'}, 'team-ipc-two')
                done = await completed(room['id'])
                self.assertEqual(done['result']['worktree'], str(work))
                self.assertEqual((work / 'built.txt').read_text(), 'Feature delivered\nFeature delivered\n')
                self.assertFalse((repo / 'built.txt').exists())
            finally:
                for _, supervisor in nodes.values(): supervisor.stopping.set()
                await asyncio.gather(*schedules)
                for server in servers:
                    server.close(); await server.wait_closed()
                await listener.close()
                for _, supervisor in nodes.values(): supervisor.store.close()
