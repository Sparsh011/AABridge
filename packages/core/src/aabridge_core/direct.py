"""Local TLS identity for app-managed, mutually authenticated connections."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import os
import re
import ssl
import subprocess
import tempfile
from pathlib import Path

from aabridge_core.config import load_config, read_private, write_private
from aabridge_protocol.messages import BridgeError, require

DEFAULT_PORT = 47631
CERTIFICATE = 'direct-cert.pem'
PRIVATE_KEY = 'direct-key.pem'


def certificate_fingerprint(pem: object) -> str:
    require(isinstance(pem, str) and len(pem) <= 8192 and
            re.fullmatch(r'-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\n?', pem),
            'Expected one public TLS certificate.')
    try:
        der = base64.b64decode(''.join(pem.splitlines()[1:-1]), validate=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
    except (ValueError, ssl.SSLError):
        raise BridgeError('invalid_request', 'Invalid public TLS certificate.') from None
    return hashlib.sha256(der).hexdigest()


def identity(state: Path) -> str:
    """Create a private app identity once; never inspect the system SSH service."""
    lock = state / 'direct-identity.lock'
    if not lock.exists():
        try:
            write_private(lock, b'', exclusive=True)
        except FileExistsError:
            pass
    read_private(lock, 0)
    descriptor = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        cert, key = state / CERTIFICATE, state / PRIVATE_KEY
        if not cert.exists() and not key.exists():
            node = load_config(state)['node_id']
            with tempfile.TemporaryDirectory(prefix='tls-', dir=state) as temporary:
                folder = Path(temporary)
                settings = folder / 'openssl.cnf'
                settings.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                                    f'[dn]\nCN={node}\n[ext]\nbasicConstraints=critical,CA:TRUE\n'
                                    'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n'
                                    'extendedKeyUsage=serverAuth,clientAuth\n')
                try:
                    subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                                    '-sha256', '-days', '3650', '-config', str(settings),
                                    '-keyout', str(folder / 'key.pem'), '-out', str(folder / 'cert.pem')],
                                   capture_output=True, check=True, timeout=20)
                except (OSError, subprocess.SubprocessError):
                    raise BridgeError('tls_identity_unavailable', 'Could not create this Mac’s app connection identity.') from None
                write_private(key, (folder / 'key.pem').read_bytes(), exclusive=True)
                write_private(cert, (folder / 'cert.pem').read_bytes(), exclusive=True)
        require(cert.exists() and key.exists(), 'Incomplete app connection identity. Review the local TLS files before retrying.', 'tls_identity_unavailable')
        read_private(key, 16384)
        pem = read_private(cert, 8192).decode()
        certificate_fingerprint(pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))  # Also verifies that the pair matches.
        return pem
    finally:
        os.close(descriptor)


def context_with_identity(state: Path, *, server: bool) -> ssl.SSLContext:
    read_private(state / PRIVATE_KEY, 16384)
    read_private(state / CERTIFICATE, 8192)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if not server:
        context.check_hostname = False  # Both ends check the exact paired certificate instead.
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(str(state / CERTIFICATE), str(state / PRIVATE_KEY))
    return context


def peer_identity(state: Path, certificate: bytes, config: dict | None = None) -> str:
    fingerprint = hashlib.sha256(certificate).hexdigest()
    peers = (config if config is not None else load_config(state))['peers']
    matches = [name for name, peer in peers.items() if name != 'local' and peer.get('enabled') is True
               and peer.get('tls_fingerprint') == fingerprint]
    require(len(matches) == 1, 'Unknown or revoked paired device.', 'unauthorized')
    return matches[0]
