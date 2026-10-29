"""Public connection cards: exchange one from each Mac for two-way pairing."""
from __future__ import annotations

import base64
import binascii
import fcntl
import getpass
import json
import os
import re
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from aabridge_protocol.messages import fields, identifier, require, text
from .config import load_config, private_directory, read_private, save_config


HOST_PUBLIC_KEY = Path('/etc/ssh/ssh_host_ed25519_key.pub')


def host_public_key():
    """macOS may generate host keys only when sshd first accepts a connection."""
    try:
        key = HOST_PUBLIC_KEY.read_text().strip()
    except FileNotFoundError:
        try:
            result = subprocess.run(['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', '127.0.0.1'],
                                    capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            result = None
        keys = []
        if result is not None and result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] == '127.0.0.1' and parts[1] == 'ssh-ed25519':
                    keys.append(' '.join(parts[1:3]))
        require(len(set(keys)) == 1,
                'AABridge could not read this Mac’s SSH host key. If Remote Login is already on, '
                'turn it off and on once in System Settings → General → Sharing, then retry. '
                'A missing host-key file does not mean Remote Login is off.', 'ssh_host_key_unavailable')
        key = keys[0]
    parts = key.split()
    require(len(parts) >= 2 and parts[0] == 'ssh-ed25519', 'This Mac’s SSH public host key is invalid.', 'ssh_host_key_unavailable')
    try:
        wire = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error):
        wire = b''
    require(wire.startswith(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20') and len(wire) == 51,
            'This Mac’s SSH public host key is invalid.', 'ssh_host_key_unavailable')
    return ' '.join(parts[:2])


def mac_name(setting):
    try:
        result = subprocess.run(['/usr/sbin/scutil', '--get', setting], capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def card(state, name='', host='', port=None):
    """Enable app-owned TLS and export only its public connection identity."""
    from aabridge_cli.actions import start_daemon
    from aabridge_core.direct import DEFAULT_PORT, certificate_fingerprint, identity
    from aabridge_protocol.messages import integer, validate_connection_card
    pem = identity(state)
    config = load_config(state)
    settings = config.get('direct', {})
    selected_port = integer(port if port is not None else settings.get('port', DEFAULT_PORT), 1, 65535)
    config['direct'] = {'enabled': True, 'bind': '0.0.0.0', 'port': selected_port}
    save_config(state, config)
    running = start_daemon(state)
    require(running.get('direct_transport') is True, 'Update this Mac with Set up this Mac before creating a direct connection card.', 'receiver_upgrade_required')
    deadline = time.monotonic() + 5
    while True:
        try:
            status = json.loads(read_private(state / 'direct-listener.json'))
        except (FileNotFoundError, ValueError):
            status = {}
        if status.get('pid') == running['pid'] and status.get('port') == selected_port:
            require(not status.get('error'), status.get('error', ''), 'direct_listener_unavailable')
            if status.get('ready'):
                break
        require(time.monotonic() < deadline, 'The app connection is not ready. Retry after setting up this Mac.', 'direct_listener_unavailable')
        time.sleep(.05)
    if not host:
        local_name = mac_name('LocalHostName')
        host = local_name + '.local' if local_name else socket.gethostname()
    return validate_connection_card({'version': 2, 'transport': 'tls', 'id': config['node_id'],
                                    'name': name or mac_name('ComputerName') or host, 'host': host,
                                    'port': status['port'], 'certificate': pem,
                                    'fingerprint': certificate_fingerprint(pem)})


def _ssh_card(state, name='', host=''):
    config = load_config(state)
    # Read the daemon's public identity before generating this device's key.
    public_host = host_public_key()
    if not host:
        local_name = mac_name('LocalHostName')
        host = local_name + '.local' if local_name else socket.gethostname()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', host) is not None,
            'Could not determine this Mac’s network address. Check its local hostname in System Settings → General → Sharing.',
            'ssh_hostname_unavailable')
    key = state / 'device-key'
    if not key.exists():
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True, timeout=10)
    read_private(key)
    return {'version': 1, 'id': config['node_id'], 'name': name or mac_name('ComputerName') or host,
            'host': host, 'user': getpass.getuser(), 'port': 22,
            'public_key': (state / 'device-key.pub').read_text().strip(), 'host_key': public_host}


def append_key(home, entry):
    directory = private_directory(home / '.ssh', create=True)
    descriptor = os.open(directory / 'authorized_keys', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o022 and info.st_size < 1048576, 'Review this Mac’s authorized_keys permissions before connecting.')
        existing = os.read(descriptor, 1048576).decode()
        if entry.strip() in existing.splitlines():
            return
        public = entry.split(' ssh-ed25519 ', 1)[1].split()[0]
        require(public not in existing, 'This key already has another SSH grant. Review it before connecting.')
        os.lseek(descriptor, 0, os.SEEK_END)
        data = memoryview((('' if not existing or existing.endswith('\n') else '\n') + entry).encode())
        while data:
            count = os.write(descriptor, data)
            require(count > 0, 'Could not save the connection.')
            data = data[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def connect(state, value, home=None):
    from aabridge_core.direct import certificate_fingerprint, identity
    from aabridge_protocol.messages import validate_connection_card
    require(isinstance(value, dict), 'Expected an AABridge connection card.')
    if value.get('version') == 1:
        return _connect_ssh(state, value, home)
    value = validate_connection_card(value)
    fingerprint = certificate_fingerprint(value['certificate'])
    require(fingerprint == value['fingerprint'], 'The connection card certificate and fingerprint differ.', 'host_identity_mismatch')
    config = load_config(state)
    peer = value['id']
    require(peer != config['node_id'], 'This is your own card. Paste the card from the other Mac.')
    require(certificate_fingerprint(identity(state)) != fingerprint,
            'This is this Mac’s own certificate. Paste the card from the other Mac.')
    existing = config['remotes'].get(peer)
    if existing and existing.get('transport') == 'tls':
        require(all(existing[key] == value[key] for key in ('host', 'port', 'fingerprint')),
                'This device’s address or certificate changed. Review the saved connection before replacing it.', 'host_identity_mismatch')
    policy = config['peers'].get(peer, {'enabled': True, 'projects': []})
    require(policy['enabled'], 'This connection was revoked; review it locally before reconnecting.', 'permission_denied')
    if policy.get('tls_fingerprint'):
        require(policy['tls_fingerprint'] == fingerprint, 'This device certificate changed. Review its saved pairing.', 'host_identity_mismatch')
    require(not any(name != peer and p.get('tls_fingerprint') == fingerprint for name, p in config['peers'].items()),
            'This certificate is already paired to another identity.', 'permission_denied')
    config['remotes'][peer] = {key: value[key] for key in ('transport', 'host', 'port', 'certificate', 'fingerprint')}
    config['remotes'][peer]['label'] = value['name']
    policy.update(tls_certificate=value['certificate'], tls_fingerprint=fingerprint, label=value['name'])
    config['peers'][peer] = policy
    save_config(state, config)
    return {'connected': peer, 'name': value['name'], 'fingerprint': fingerprint, 'transport': 'tls',
            'note': 'Exchange both cards and allow the paired Mac in Projects. Connection saved; reachability is checked when you use it.'}


def _connect_ssh(state, value, home=None):
    from aabridge_cli.actions import enroll_remote, grant_peer
    value = fields(value, {'version', 'id', 'name', 'host', 'user', 'port', 'public_key', 'host_key'})
    require(value['version'] == 1, 'Unsupported connection card.')
    peer = identifier(value['id'])
    config = load_config(state)
    require(peer != config['node_id'], 'This is your own card. Paste the card from the other Mac.')
    require(config['remotes'].get(peer, {}).get('transport') != 'tls', 'This device uses direct TLS. Use its current connection card; an SSH downgrade is not permitted.')
    text(value['name'], 120)
    for field in ('public_key', 'host_key'):
        require(isinstance(value[field], str) and len(value[field]) < 4096 and '\n' not in value[field] and '\r' not in value[field] and value[field].startswith('ssh-ed25519 '), 'Invalid public key in connection card.')
    require(type(value['port']) is int and 1 <= value['port'] <= 65535, 'Invalid SSH port.')
    # ssh_arguments validates host/user before any filesystem change.
    require(isinstance(value["host"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", value["host"]), "Invalid Mac address.")
    require(isinstance(value["user"], str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", value["user"]), "Invalid Mac user.")
    require((state / 'device-key').exists(), 'Create this Mac’s card first, then connect the other Mac.')
    host = value['host'] if value['port'] == 22 else f"[{value['host']}]:{value['port']}"
    with tempfile.TemporaryDirectory() as tmp:
        public = Path(tmp) / 'peer.pub'
        public.write_text(value['public_key'] + '\n')
        known = Path(tmp) / 'known_hosts'
        line = host + ' ' + ' '.join(value['host_key'].split()[:2]) + '\n'
        known.write_text(line)
        result = subprocess.run(['/usr/bin/ssh-keygen', '-lf', str(known)], capture_output=True, text=True, timeout=5)
        require(result.returncode == 0, 'Invalid Mac host key.')
        fingerprint = result.stdout.split()[1]
        if peer not in config['remotes']:
            enroll_remote(state, peer, value['host'], value['user'], value['port'], state / 'device-key', known, fingerprint)
        else:
            remote = config['remotes'][peer]
            require(all(remote[key] == value[key] for key in ('host', 'user', 'port')) and read_private(Path(remote['known_hosts'])).decode() == line,
                    'This Mac’s address or host key changed. Review the saved connection before replacing it.', 'host_identity_mismatch')
        config = load_config(state)
        if peer not in config['peers']:
            grant_peer(state, peer, [], public)
        require(load_config(state)['peers'][peer]['enabled'], 'This connection was revoked; review it before reconnecting.', 'permission_denied')
        entry = read_private(state / f'authorized-key-{peer}.txt').decode()
        require(' '.join(value['public_key'].split()[:2]) in entry, 'The saved peer key differs from this card.')
        append_key(home or Path.home(), entry)
        config = load_config(state)
        config['remotes'][peer]['label'] = value['name']
        config['peers'][peer]['label'] = value['name']
        save_config(state, config)
    return {'connected': peer, 'name': value['name'], 'fingerprint': fingerprint,
            'note': 'Exchange both cards, then choose which projects this Mac shares in Projects.'}
