"""Public connection cards: exchange one from each Mac for two-way pairing."""
from __future__ import annotations

import fcntl
import getpass
import json
import os
import re
import socket
import stat
import subprocess
import tempfile
from pathlib import Path

from aabridge_protocol.messages import fields, identifier, require, text
from .config import load_config, private_directory, read_private, save_config


def card(state, name='', host=''):
    config = load_config(state)
    key = state / 'device-key'
    if not key.exists():
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True, timeout=10)
    read_private(key)
    host_key = Path('/etc/ssh/ssh_host_ed25519_key.pub')
    require(host_key.is_file(), 'Turn on Remote Login in macOS Settings, then create the connection card again.', 'ssh_setup')
    return {'version': 1, 'id': config['node_id'], 'name': name or socket.gethostname(),
            'host': host or socket.gethostname(), 'user': getpass.getuser(), 'port': 22,
            'public_key': (state / 'device-key.pub').read_text().strip(), 'host_key': host_key.read_text().strip()}


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
    from aabridge_cli.actions import enroll_remote, grant_peer
    value = fields(value, {'version', 'id', 'name', 'host', 'user', 'port', 'public_key', 'host_key'})
    require(value['version'] == 1, 'Unsupported connection card.')
    peer = identifier(value['id'])
    config = load_config(state)
    require(peer != config['node_id'], 'This is your own card. Paste the card from the other Mac.')
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
