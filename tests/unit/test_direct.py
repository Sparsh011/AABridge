from __future__ import annotations

import asyncio
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_cli.actions import demo_init, fetch_result, request, send
from aabridge_core.config import authenticate, initialize, load_config, read_private, save_config
from aabridge_core.direct import CERTIFICATE, PRIVATE_KEY, certificate_fingerprint, identity
from aabridge_core.pairing import card, connect
from aabridge_daemon.direct import DirectListener
from aabridge_daemon.server import Supervisor
from aabridge_mcp.server import handle as mcp_handle
from aabridge_protocol.messages import BridgeError, canonical, validate_connection_card
from aabridge_transport_direct.client import call, connection, fetch_bundle, response
from aabridge_transport_local.client import call as local_call
from aabridge_workspace.git import git
from test_service import DeterministicWorker


def public_card(state, port):
    pem = read_private(state / CERTIFICATE).decode()
    return {'version': 2, 'transport': 'tls', 'id': load_config(state)['node_id'], 'name': state.name,
            'host': '127.0.0.1', 'port': port, 'certificate': pem, 'fingerprint': certificate_fingerprint(pem)}


class DirectTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-direct-')
        self.root = Path(self.temp.name).resolve()
        self.states, self.supervisors, self.listeners, self.cards = [], [], [], []
        for name in ('book', 'mini'):
            state = self.root / name
            initialize(state)
            await asyncio.to_thread(identity, state)
            supervisor = Supervisor(state, worker_factory=DeterministicWorker)
            supervisor.isolation_checked = True
            listener = DirectListener(supervisor)
            port = await listener.start('127.0.0.1', 0)
            self.states.append(state); self.supervisors.append(supervisor); self.listeners.append(listener)
            self.cards.append(public_card(state, port))
        with patch('aabridge_core.pairing.append_key', side_effect=AssertionError('SSH keys must not be installed')):
            for index, state in enumerate(self.states):
                connect(state, self.cards[1-index])
        self.book, self.mini = self.states
        self.book_id, self.mini_id = (value['id'] for value in self.cards)
        demo_init(self.mini, self.root / 'source', 'demo')
        self.allow(self.mini, self.book_id, ['demo'])
        config = load_config(self.mini)
        config['projects']['demo']['execution_enabled'] = True
        save_config(self.mini, config)

    async def asyncTearDown(self):
        for listener in self.listeners:
            await listener.close()
        for supervisor in self.supervisors:
            supervisor.store.close()
        self.temp.cleanup()

    def allow(self, state, peer, projects):
        config = load_config(state)
        config['peers'][peer]['projects'] = projects
        save_config(state, config)

    async def rpc(self, op, body=None, key=None):
        return await asyncio.to_thread(send, self.book, request(op, body or {}, key), self.mini_id)

    async def submit(self):
        project = (await self.rpc('projects'))['demo']
        return await self.rpc('submit', {'project_id': 'demo', 'recipient': 'codex', 'base_commit': project['base_commit'],
                                       'objective': 'Fix addition', 'scope': project['scope'], 'acceptance': ['Pass fixed checks'],
                                       'context': [], 'limits': project['limits'], 'permission_profile': 'proposal-v1'}, 'direct-submit')

    async def completed(self):
        task = await self.submit()
        await self.supervisors[1].run_task(task)
        question = await self.rpc('status', {'task_id': task['id']})
        await self.rpc('answer', {'task_id': task['id'], 'revision': question['revision'],
                                 'question_id': question['question']['id'], 'text': 'Yes, integer addition'}, 'direct-answer')
        await self.supervisors[1].run_task(self.supervisors[1].store.get(task['id']))
        return await self.rpc('status', {'task_id': task['id']})

    async def test_pairing_is_public_retry_safe_preserves_permissions_and_never_calls_ssh(self):
        with patch('subprocess.run', side_effect=AssertionError('No shell/SSH subprocess per RPC')):
            reply = await self.rpc('ping')
        self.assertEqual(reply['node_id'], self.mini_id)
        self.assertNotIn('PRIVATE KEY', json.dumps(self.cards))
        connect(self.mini, self.cards[0])
        self.assertEqual(load_config(self.mini)['peers'][self.book_id]['projects'], ['demo'])
        self.assertFalse((self.mini / 'device-key').exists())
        self.assertFalse((self.mini / '.ssh').exists())
        with self.assertRaises(BridgeError):
            authenticate(load_config(self.mini), 'not-a-token')
        changed = dict(self.cards[0], host='another.local')
        with self.assertRaises(BridgeError):
            connect(self.mini, changed)
        with self.assertRaises(BridgeError):
            connect(self.book, self.cards[0])

    async def test_question_answer_history_and_exact_git_result_transfer(self):
        task = await self.completed()
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(task['sender'], self.book_id)
        self.assertEqual(task['thread_id'], 'thread-' + task['id'])
        history = await self.rpc('conversation', {'task_id': task['id'], 'cursor': 0})
        self.assertIn('integer addition', json.dumps(history))
        review = await asyncio.to_thread(fetch_result, self.book, self.mini_id, task['id'])
        self.assertEqual(git(Path(review['review_repository']), 'rev-parse', 'FETCH_HEAD'), task['result']['result_commit'])
        self.assertIn('return a + b', review['diff'])
        self.assertIn('return a - b', (self.root / 'source/math_ops.py').read_text())
        # The same submission key/body replays instead of creating another task.
        self.assertEqual((await self.submit())['id'], task['id'])

    async def test_mcp_routes_to_tls_and_remote_admin_operations_stay_denied(self):
        cli = await asyncio.to_thread(subprocess.run, [sys.executable, '-m', 'aabridge_cli', 'project', 'list',
                                                       '--state-dir', str(self.book), '--remote', self.mini_id],
                                      capture_output=True, text=True, check=True)
        self.assertIn('demo', json.loads(cli.stdout))
        result = await asyncio.to_thread(mcp_handle, self.book, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                         'params': {'name': 'bridge_projects', 'arguments': {'body': {}, 'remote': self.mini_id}}})
        self.assertIn('demo', json.dumps(result))
        for op in ('shutdown', 'teams'):
            with self.assertRaises(BridgeError) as error:
                await self.rpc(op)
            self.assertEqual(error.exception.code, 'permission_denied')

    async def test_receiver_local_coding_history_is_visible_from_the_other_mac(self):
        config = load_config(self.mini)
        project = config['projects']['demo']
        project.update(permission_profile='coding-v1', scope=['*'])
        save_config(self.mini, config)
        body = {'project_id': 'demo', 'recipient': 'codex', 'base_commit': project['base_commit'],
                'objective': 'Task created in the mini app', 'scope': ['*'], 'acceptance': ['Fixture history'],
                'context': [], 'limits': project['limits'], 'permission_profile': 'coding-v1'}
        task = self.supervisors[1].store.handle(request('submit', body, 'mini-local'), 'local', config)
        self.supervisors[1].store.update(task['id'], 'completed-fixture', status='completed')
        self.assertEqual((await self.rpc('tasks', {'cursor': 0}))['tasks'], [])
        shared = await self.rpc('tasks', {'cursor': 0, 'shared': True})
        self.assertEqual(shared['tasks'][0]['id'], task['id'])
        detail = await self.rpc('status', {'task_id': task['id'], 'shared': True})
        self.assertEqual(detail['sender'], 'local')
        self.assertFalse(detail['permissions']['can_control'])
        history = await self.rpc('conversation', {'task_id': task['id'], 'cursor': 0, 'shared': True})
        self.assertIn('Task created in the mini app', json.dumps(history))
        mcp = await asyncio.to_thread(mcp_handle, self.book, {'jsonrpc': '2.0', 'id': 10, 'method': 'tools/call',
                                      'params': {'name': 'bridge_tasks', 'arguments': {'body': {'cursor': 0, 'shared': True}, 'remote': self.mini_id}}})
        self.assertIn(task['id'], json.dumps(mcp))
        cli = await asyncio.to_thread(subprocess.run, [sys.executable, '-m', 'aabridge_cli', 'task', 'status',
                                      '--state-dir', str(self.book), '--remote', self.mini_id, '--id', task['id'], '--shared'],
                                      capture_output=True, text=True, check=True)
        self.assertFalse(json.loads(cli.stdout)['permissions']['can_control'])
        with self.assertRaises(BridgeError) as denied:
            await self.rpc('cancel', {'task_id': task['id']})
        self.assertEqual(denied.exception.code, 'permission_denied')

    async def test_project_grants_and_revocation_apply_to_rpc_and_result_reads(self):
        task = await self.completed()
        self.allow(self.mini, self.book_id, [])
        self.assertEqual(await self.rpc('projects'), {})
        with self.assertRaises(BridgeError):
            await self.rpc('status', {'task_id': task['id']})
        remote = load_config(self.book)['remotes'][self.mini_id]
        with self.assertRaises(BridgeError):
            await asyncio.to_thread(fetch_bundle, self.book, remote, task['id'], task['result']['result_commit'], self.root / 'denied.bundle')
        config = load_config(self.mini)
        config['peers'][self.book_id]['enabled'] = False
        save_config(self.mini, config)
        with self.assertRaises(BridgeError):
            await self.rpc('ping')
        with self.assertRaises(BridgeError):
            connect(self.mini, self.cards[0])

    async def test_unpaired_and_certificate_mismatch_fail_closed(self):
        rogue = self.root / 'rogue'; initialize(rogue)
        await asyncio.to_thread(identity, rogue)
        remote = load_config(self.book)['remotes'][self.mini_id]
        with self.assertRaises(BridgeError):
            await asyncio.to_thread(call, rogue, remote, request('ping', {}))
        def without_certificate():
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.load_verify_locations(cadata=remote['certificate'])
            with socket.create_connection((remote['host'], remote['port']), timeout=2) as raw:
                with context.wrap_socket(raw, server_hostname='aabridge') as stream:
                    stream.sendall((canonical({'version': 1, 'kind': 'rpc', 'request': request('ping', {}).to_dict()}) + '\n').encode())
                    with stream.makefile('rb') as incoming:
                        return response(incoming)
        with self.assertRaises((BridgeError, OSError)):
            await asyncio.to_thread(without_certificate)
        changed = {**remote, 'certificate': read_private(rogue / CERTIFICATE).decode()}
        changed['fingerprint'] = certificate_fingerprint(changed['certificate'])
        with self.assertRaises(BridgeError) as error:
            await asyncio.to_thread(call, self.book, changed, request('ping', {}))
        self.assertEqual(error.exception.code, 'host_identity_mismatch')
        # A connection card cannot smuggle private keys, certificates or a new pin.
        for modified in (dict(self.cards[0], fingerprint='0' * 64),
                         dict(self.cards[0], certificate=self.cards[0]['certificate'] + self.cards[1]['certificate']),
                         dict(self.cards[0], id='local')):
            with self.assertRaises(BridgeError):
                connect(self.mini, modified)

    async def test_a_certificate_signed_by_a_paired_device_cannot_impersonate_it(self):
        rogue = self.root / 'signed-child'; initialize(rogue)
        await asyncio.to_thread(identity, rogue)
        commands = [
            ['/usr/bin/openssl', 'req', '-new', '-key', str(rogue / PRIVATE_KEY), '-subj', '/CN=unpaired-child', '-out', str(rogue / 'request.pem')],
            ['/usr/bin/openssl', 'x509', '-req', '-in', str(rogue / 'request.pem'), '-CA', str(self.book / CERTIFICATE),
             '-CAkey', str(self.book / PRIVATE_KEY), '-set_serial', '2', '-days', '1', '-out', str(rogue / CERTIFICATE)],
        ]
        for command in commands:
            await asyncio.to_thread(subprocess.run, command, capture_output=True, check=True)
        remote = load_config(self.book)['remotes'][self.mini_id]
        with self.assertRaises(BridgeError) as error:
            await asyncio.to_thread(call, rogue, remote, request('ping', {}))
        self.assertEqual(error.exception.code, 'unauthorized')

    async def test_another_paired_peer_with_the_same_project_cannot_read_my_task(self):
        task = await self.completed()
        other = self.root / 'other'; initialize(other)
        await asyncio.to_thread(identity, other)
        other_card = public_card(other, 1)
        connect(self.mini, other_card)
        connect(other, self.cards[1])
        self.allow(self.mini, other_card['id'], ['demo'])
        with self.assertRaises(BridgeError) as error:
            await asyncio.to_thread(send, other, request('status', {'task_id': task['id']}), self.mini_id)
        self.assertEqual(error.exception.code, 'permission_denied')

    async def test_unknown_oversized_and_path_result_requests_are_rejected(self):
        remote = load_config(self.book)['remotes'][self.mini_id]
        def raw(payload):
            with connection(self.book, remote) as stream:
                stream.sendall(payload)
                with stream.makefile('rb') as incoming:
                    return response(incoming)
        payloads = [b'{"version":1,"kind":"shell","command":"whoami"}\n', b'x' * 131074 + b'\n',
                    (canonical({'version': 1, 'kind': 'result_bundle', 'task_id': '../config.json', 'commit': 'a' * 40}) + '\n').encode()]
        for payload in payloads:
            with self.assertRaises(BridgeError):
                await asyncio.to_thread(raw, payload)

    async def test_ssh_upgrade_preserves_grants_and_rejects_a_silent_downgrade(self):
        config = load_config(self.book)
        config['remotes'][self.mini_id] = {'host': 'legacy.local', 'user': 'legacy', 'port': 22}
        config['peers'][self.mini_id]['projects'] = ['kept-project']
        save_config(self.book, config)
        connect(self.book, self.cards[1])
        self.assertEqual(load_config(self.book)['peers'][self.mini_id]['projects'], ['kept-project'])
        self.assertEqual(load_config(self.book)['remotes'][self.mini_id]['transport'], 'tls')
        with self.assertRaises(BridgeError):
            connect(self.book, {'version': 1, 'id': self.mini_id, 'name': 'mini', 'host': 'legacy.local',
                                'port': 22, 'user': 'legacy', 'host_key': '', 'public_key': ''})

    async def test_unreachable_direct_device_never_requests_remote_login(self):
        remote = dict(load_config(self.book)['remotes'][self.mini_id])
        with socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1', 0))  # Reserved but not listening.
            remote['port'] = unavailable.getsockname()[1]
            with self.assertRaises(BridgeError) as error:
                await asyncio.to_thread(call, self.book, remote, request('ping', {}))
        self.assertEqual(error.exception.code, 'direct_unavailable')
        self.assertNotIn('Remote Login', error.exception.message)


class DirectCardDaemonTests(unittest.TestCase):
    def test_card_starts_app_listener_without_system_ssh(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-direct-daemon-') as temporary:
            state = Path(temporary).resolve() / 'state'
            initialize(state)
            with socket.socket() as available:
                available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
            try:
                with patch('aabridge_core.pairing.host_public_key', side_effect=AssertionError('No system SSH')):
                    value = card(state, host='127.0.0.1', port=port)
                self.assertEqual(value['transport'], 'tls')
                self.assertEqual(value['port'], port)
                validate_connection_card(value)
                self.assertNotIn('user', value)
                self.assertTrue(json.loads(read_private(state / 'direct-listener.json'))['ready'])
                self.assertEqual(card(state, host='127.0.0.1'), value)
            finally:
                try:
                    local_call(state, request('shutdown', {}))
                except BridgeError:
                    pass
                import time
                for _ in range(100):
                    if (state / 'direct-listener.json').exists() and not json.loads(read_private(state / 'direct-listener.json')).get('ready'):
                        break
                    time.sleep(.05)
