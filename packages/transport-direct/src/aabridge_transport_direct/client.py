"""Bounded direct TLS RPC and Git bundle reads; no shell or SSH subprocess."""
from __future__ import annotations

import hashlib
import json
import socket
import ssl
from contextlib import contextmanager
from pathlib import Path

from aabridge_core.direct import certificate_fingerprint, context_with_identity
from aabridge_protocol.messages import BridgeError, Request, canonical, fields, integer, require, validate_direct_envelope

MAX_RESPONSE = 262144
MAX_BUNDLE = 64 * 1024 * 1024


@contextmanager
def connection(state: Path, remote: dict):
    context = context_with_identity(state, server=False)
    expected = certificate_fingerprint(remote['certificate'])
    require(expected == remote['fingerprint'], 'Saved device certificate changed.', 'host_identity_mismatch')
    context.load_verify_locations(cadata=remote['certificate'])
    try:
        with socket.create_connection((remote['host'], remote['port']), timeout=8) as raw:
            with context.wrap_socket(raw, server_hostname='aabridge') as stream:
                require(hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() == expected,
                        'Device identity did not match the paired certificate.', 'host_identity_mismatch')
                stream.settimeout(40)
                yield stream
    except ssl.SSLCertVerificationError:
        raise BridgeError('host_identity_mismatch', 'Device certificate did not match the paired identity or has expired. Review its connection card.') from None
    except (OSError, TimeoutError):
        # Keep this distinct from legacy SSH failures so older UI error maps
        # cannot replace the message with instructions to enable Remote Login.
        raise BridgeError('direct_unavailable', 'Direct connection failed. Check that AABridge is running on the other Mac and its LAN/VPN address is reachable. A running task is preserved.') from None


def response(stream) -> dict:
    line = stream.readline(MAX_RESPONSE + 1)
    require(line.endswith(b'\n') and len(line) <= MAX_RESPONSE, 'Invalid or oversized direct response.', 'remote_protocol_error')
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError):
        raise BridgeError('remote_protocol_error', 'Malformed direct response.') from None
    require(isinstance(value, dict) and set(value) in ({'result'}, {'error'}), 'Invalid direct response.', 'remote_protocol_error')
    if 'error' in value:
        error = fields(value['error'], {'code', 'message'})
        require(isinstance(error['code'], str) and isinstance(error['message'], str), 'Invalid direct error.')
        raise BridgeError(error['code'], error['message'])
    return value['result']


def call(state: Path, remote: dict, request: Request) -> dict:
    envelope = {'version': 1, 'kind': 'rpc', 'request': request.to_dict()}
    validate_direct_envelope(envelope)
    with connection(state, remote) as stream:
        stream.sendall((canonical(envelope) + '\n').encode())
        with stream.makefile('rb') as incoming:
            return response(incoming)


def fetch_bundle(state: Path, remote: dict, task_id: str, commit: str, destination: Path) -> None:
    envelope = {'version': 1, 'kind': 'result_bundle', 'task_id': task_id, 'commit': commit}
    validate_direct_envelope(envelope)
    with connection(state, remote) as stream:
        stream.sendall((canonical(envelope) + '\n').encode())
        with stream.makefile('rb') as incoming:
            metadata = fields(response(incoming), {'size', 'sha256'})
            size = integer(metadata['size'], 1, MAX_BUNDLE)
            digest = hashlib.sha256()
            with destination.open('xb') as output:
                while size:
                    chunk = incoming.read(min(size, 65536))
                    require(bool(chunk), 'Incomplete Git result transfer.', 'git_transfer_failed')
                    output.write(chunk)
                    digest.update(chunk)
                    size -= len(chunk)
            require(digest.hexdigest() == metadata['sha256'], 'Git result transfer checksum mismatch.', 'git_transfer_failed')
