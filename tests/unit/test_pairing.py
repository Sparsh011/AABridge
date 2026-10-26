from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from aabridge_core.config import initialize, load_config, read_private
from aabridge_core.pairing import connect
from aabridge_protocol.messages import BridgeError


class TwoWayPairingTests(unittest.TestCase):
    def test_public_card_exchange_enrolls_both_directions_and_is_retry_safe(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-pair-') as tmp:
            root = Path(tmp).resolve()
            states = [root / 'macbook', root / 'mini']
            cards = []
            for index, state in enumerate(states):
                node = initialize(state)['node_id']
                for key in ('device-key', 'host-key'):
                    subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(state / key)], check=True)
                cards.append({'version':1,'id':node,'name':state.name,'host':state.name + '.local','user':'fixture','port':22,
                              'public_key':(state / 'device-key.pub').read_text().strip(), 'host_key':(state / 'host-key.pub').read_text().strip()})
            for index, state in enumerate(states):
                home = root / ('home-' + str(index)); home.mkdir(mode=0o700)
                other = cards[1-index]
                connect(state, other, home)
                connect(state, other, home)
                config = load_config(state)
                self.assertIn(other['id'], config['remotes'])
                self.assertEqual(config['peers'][other['id']]['projects'], [])
                entries = (home / '.ssh/authorized_keys').read_text().splitlines()
                self.assertEqual(len(entries), 1)
                self.assertTrue(entries[0].startswith('restrict,command='))
                self.assertNotIn('PRIVATE KEY', str(other))
                changed = dict(other, host_key=cards[index]['host_key'])
                with self.assertRaises(BridgeError):
                    connect(state, changed, home)
                with self.assertRaises(BridgeError):
                    connect(state, cards[index], home)


class ConnectionCardCreationTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.patch = patch
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp', prefix='aab-card-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        initialize(self.state)
        self.host_key = self.root / 'host-key'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.host_key)], check=True)
        self.public = (self.root / 'host-key.pub').read_text().strip()

    def test_existing_host_key_needs_no_ssh_scan(self):
        from aabridge_core.pairing import host_public_key
        with self.patch('aabridge_core.pairing.HOST_PUBLIC_KEY', self.root / 'host-key.pub'), self.patch('aabridge_core.pairing.subprocess.run') as run:
            self.assertEqual(host_public_key(), ' '.join(self.public.split()[:2]))
            run.assert_not_called()

    def test_missing_public_file_reads_live_loopback_daemon(self):
        from aabridge_core.pairing import host_public_key
        result = subprocess.CompletedProcess([], 0, '# banner\n127.0.0.1 ' + self.public + '\n', '')
        with self.patch('aabridge_core.pairing.HOST_PUBLIC_KEY', self.root / 'absent.pub'), self.patch('aabridge_core.pairing.subprocess.run', return_value=result) as run:
            self.assertEqual(host_public_key(), ' '.join(self.public.split()[:2]))
            self.assertEqual(run.call_args.args[0], ['/usr/bin/ssh-keyscan', '-T', '5', '-t', 'ed25519', '127.0.0.1'])
            self.assertEqual(run.call_args.kwargs['timeout'], 8)

    def test_unreachable_listener_does_not_claim_remote_login_is_off(self):
        from aabridge_core.pairing import host_public_key
        result = subprocess.CompletedProcess([], 1, '', 'connection refused')
        with self.patch('aabridge_core.pairing.HOST_PUBLIC_KEY', self.root / 'absent.pub'), self.patch('aabridge_core.pairing.subprocess.run', return_value=result):
            with self.assertRaises(BridgeError) as error:
                host_public_key()
            self.assertEqual(error.exception.code, 'ssh_host_key_unavailable')
            self.assertIn('If Remote Login is already on', error.exception.message)

    def test_missing_file_and_scan_timeout_has_an_actionable_error(self):
        from aabridge_core.pairing import host_public_key
        with self.patch('aabridge_core.pairing.HOST_PUBLIC_KEY', self.root / 'absent.pub'), self.patch('aabridge_core.pairing.subprocess.run', side_effect=subprocess.TimeoutExpired('ssh-keyscan', 8)):
            with self.assertRaises(BridgeError) as error:
                host_public_key()
            self.assertEqual(error.exception.code, 'ssh_host_key_unavailable')

    def test_card_uses_network_hostname_instead_of_display_hostname(self):
        from aabridge_core.pairing import card
        with self.patch('aabridge_core.pairing.host_public_key', return_value=self.public), self.patch('aabridge_core.pairing.mac_name', side_effect=lambda field: {'LocalHostName':'Sparsh-MBP', 'ComputerName':"Sparsh's MBP"}[field]), self.patch('aabridge_core.pairing.socket.gethostname', return_value="Sparsh's-MBP"):
            value = card(self.state)
            self.assertEqual(value['host'], 'Sparsh-MBP.local')
            self.assertEqual(value['name'], "Sparsh's MBP")
            self.assertEqual(set(value), {'version','id','name','host','user','port','public_key','host_key'})

    def test_malformed_local_public_key_is_not_put_in_a_card(self):
        from aabridge_core.pairing import host_public_key
        (self.root / 'broken.pub').write_text('ssh-ed25519 not-a-public-key')
        with self.patch('aabridge_core.pairing.HOST_PUBLIC_KEY', self.root / 'broken.pub'), self.assertRaises(BridgeError):
            host_public_key()
