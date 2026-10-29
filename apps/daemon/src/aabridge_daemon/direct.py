"""App-owned TLS listener. Remote requests retain peer and project policy."""
from __future__ import annotations

import asyncio
import hashlib
import os
import ssl
import tempfile
from pathlib import Path

from aabridge_core.config import load_config, read_private, write_private
from aabridge_core.direct import CERTIFICATE, context_with_identity, peer_identity
from aabridge_protocol.messages import BridgeError, MAX_MESSAGE, canonical, decode, require, validate_direct_envelope, validate_request
from aabridge_transport_direct.client import MAX_BUNDLE, MAX_RESPONSE
from aabridge_workspace.git import git


class DirectListener:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.state = supervisor.state
        self.connections = set()
        self.server = None
        self.contexts = {}

    def context(self):
        config = load_config(self.state)
        # Include our own certificate so an empty trust store never falls back
        # to system CAs. Local identity is never accepted as a remote peer.
        certificates = [read_private(self.state / CERTIFICATE, 8192).decode()]
        certificates += sorted({peer['tls_certificate'] for name, peer in config['peers'].items()
                                if name != 'local' and peer.get('enabled') and peer.get('tls_certificate')})
        key = tuple(certificates)
        if key not in self.contexts:
            context = context_with_identity(self.state, server=True)
            context.load_verify_locations(cadata='\n'.join(certificates))
            self.contexts = {key: context}  # Discard stale enrollment contexts.
        return self.contexts[key]

    async def start(self, host, port):
        context = self.context()
        def paired_context(connection, name, original):
            try:
                connection.context = self.context()  # New pairings/revocations need no daemon restart.
            except (OSError, ValueError, BridgeError, ssl.SSLError):
                return ssl.ALERT_DESCRIPTION_ACCESS_DENIED
        context.sni_callback = paired_context
        self.server = await asyncio.start_server(self.accept, host, port, ssl=context,
                                                ssl_handshake_timeout=5, limit=MAX_MESSAGE + 1,
                                                backlog=16)
        return self.server.sockets[0].getsockname()[1]

    def authorized_result(self, identity, task_id, commit):
        config = load_config(self.state)
        task = self.supervisor.store.handle(validate_request({'version': 1, 'op': 'status', 'key': 'result-read',
                                                            'body': {'task_id': task_id}}), identity, config)
        require(task['status'] == 'completed' and task['result']['result_commit'] == commit,
                'No completed authorized result is available.', 'permission_denied')
        path = self.state / 'workspaces' / task_id
        require(path.resolve() == path and (path / '.git').is_dir() and not (path / '.git').is_symlink(),
                'Unexpected result workspace.', 'unsafe_workspace')
        return path

    @staticmethod
    def make_bundle(path, commit, bundle):
        require(git(path, 'rev-parse', 'HEAD') == commit, 'Result workspace identity changed.', 'git_transfer_failed')
        git(path, 'bundle', 'create', str(bundle), 'HEAD')
        require(bundle.stat().st_size <= MAX_BUNDLE, 'Git result exceeds the 64 MiB direct-transfer limit.', 'git_transfer_failed')
        digest = hashlib.sha256()
        with bundle.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        return {'size': bundle.stat().st_size, 'sha256': digest.hexdigest()}

    async def accept(self, reader, writer):
        current = asyncio.current_task()
        self.connections.add(current)
        sent = False
        try:
            require(len(self.connections) <= 16, 'Too many direct connections.', 'rate_limited')
            certificate = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            identity = peer_identity(self.state, certificate)
            data = await asyncio.wait_for(reader.readline(), 5)
            envelope = validate_direct_envelope(decode(data))
            # Check enrollment again after reading, so revocation closes an idle request.
            config = load_config(self.state)
            require(peer_identity(self.state, certificate, config) == identity, 'Device enrollment changed.', 'unauthorized')
            if envelope['kind'] == 'rpc':
                result = self.supervisor.store.handle(validate_request(envelope['request']), identity, config)
                encoded = (canonical({'result': result}) + '\n').encode()
                require(len(encoded) <= MAX_RESPONSE, 'Response exceeds limit.', 'response_too_large')
                writer.write(encoded)
                await asyncio.wait_for(writer.drain(), 5)
            else:
                path = self.authorized_result(identity, envelope['task_id'], envelope['commit'])
                with tempfile.TemporaryDirectory(prefix='result-bundle-', dir=self.state) as temporary:
                    bundle = Path(temporary) / 'result.bundle'
                    metadata = await asyncio.to_thread(self.make_bundle, path, envelope['commit'], bundle)
                    require(peer_identity(self.state, certificate) == identity, 'Device enrollment changed.', 'unauthorized')
                    self.authorized_result(identity, envelope['task_id'], envelope['commit'])
                    writer.write((canonical({'result': metadata}) + '\n').encode())
                    sent = True
                    with bundle.open('rb') as stream:
                        while chunk := stream.read(65536):
                            writer.write(chunk)
                            await asyncio.wait_for(writer.drain(), 5)
        except BridgeError as exc:
            if not sent:
                writer.write((canonical({'error': {'code': exc.code, 'message': exc.message}}) + '\n').encode())
        except (ValueError, OSError, asyncio.TimeoutError, asyncio.LimitOverrunError):
            if not sent:
                writer.write(b'{"error":{"code":"invalid_request","message":"Invalid, oversized, or timed-out direct request."}}\n')
        except Exception:
            if not sent:
                writer.write(b'{"error":{"code":"internal_error","message":"Direct request failed; no raw logs were returned."}}\n')
        finally:
            try:
                await asyncio.wait_for(writer.drain(), 5)
            except (OSError, asyncio.TimeoutError):
                pass
            writer.close()
            self.connections.discard(current)

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        pending = tuple(self.connections)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def manage(supervisor):
    listener = DirectListener(supervisor)
    published = False
    active = None
    try:
        while not supervisor.stopping.is_set():
            delay = .2
            settings = load_config(supervisor.state).get('direct', {})
            selected = (settings.get('bind', '0.0.0.0'), settings.get('port')) if settings.get('enabled') else None
            if active != selected:
                await listener.close()
                listener = DirectListener(supervisor)
                active = selected
                if published:
                    write_private(supervisor.state / 'direct-listener.json', canonical({'ready': False, 'pid': os.getpid()}).encode())
            if settings.get('enabled') and listener.server is None:
                status = {'ready': False, 'pid': os.getpid(), 'port': settings['port']}
                try:
                    port = await listener.start(settings.get('bind', '0.0.0.0'), settings['port'])
                    status.update(ready=True, port=port)
                except (OSError, ValueError, BridgeError, ssl.SSLError):
                    status['error'] = 'The app connection could not start. Check its local TLS identity and whether another app is using the port; choose another port if needed.'
                    delay = 1
                write_private(supervisor.state / 'direct-listener.json', (canonical(status) + '\n').encode())
                published = True
            await asyncio.sleep(delay)
    finally:
        await listener.close()
        if published:
            write_private(supervisor.state / 'direct-listener.json', canonical({'ready': False, 'pid': os.getpid()}).encode())
