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
