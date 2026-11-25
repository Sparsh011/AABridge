import json
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_protocol.messages import BridgeError
from aabridge_workspace import publish

URL = 'https://github.com/o/service/pull/5'
APPROVED = 'a' * 40


def state(**changes):
    info = {'state': 'OPEN', 'isDraft': False, 'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN', 'reviewDecision': '',
            'headRefName': 'codex/relay', 'headRefOid': APPROVED, 'baseRefName': 'master', 'number': 5}
    info.update(changes)
    return info


def gh_with(info, merge_error='X Pull request is not mergeable'):
    def fake(repo, *arguments, stdin=None, failure):
        if arguments[:2] == ('pr', 'merge'):
            raise BridgeError('publish_failed', failure + ' ' + merge_error)
        if arguments[:2] == ('pr', 'view'):
            if info is None:
                raise BridgeError('publish_failed', failure)
            return json.dumps(info)
        raise AssertionError(arguments)
    return fake


class MergeErrorTests(unittest.TestCase):
    def merge_error(self, info):
        with patch.object(publish, '_gh', side_effect=gh_with(info)):
            with self.assertRaises(BridgeError) as caught:
                publish.merge(Path('.'), URL, APPROVED)
        return caught.exception

    def test_conflict_names_both_branches_and_proposes_fixes(self):
        error = self.merge_error(state(mergeable='CONFLICTING', mergeStateStatus='DIRTY'))
        self.assertEqual(error.code, 'merge_blocked')
        self.assertIn('`codex/relay` conflicts with `master`', error.message)
        self.assertIn('How to fix:\n1. ', error.message)
        self.assertIn('gh pr checkout 5 && git fetch origin master && git merge origin/master', error.message)

    def test_moved_head_is_reported_with_both_commits(self):
        error = self.merge_error(state(headRefOid='b' * 40))
        self.assertIn('now points to bbbbbbb', error.message)
        self.assertIn('approved (aaaaaaa)', error.message)

    def test_each_blocker_has_a_reason_and_a_fix(self):
        for info, words in [(state(isDraft=True), 'draft'), (state(state='CLOSED'), 'closed'),
                            (state(mergeStateStatus='BEHIND'), 'behind'),
                            (state(mergeStateStatus='BLOCKED', reviewDecision='REVIEW_REQUIRED'), 'approving review'),
                            (state(mergeStateStatus='BLOCKED'), 'status check'),
                            (state(mergeable='UNKNOWN', mergeStateStatus='UNKNOWN'), 'still working')]:
            with self.subTest(words):
                error = self.merge_error(info)
                self.assertIn(words, error.message)
                self.assertIn('How to fix:', error.message)

    def test_already_merged_approved_commit_counts_as_merged(self):
        with patch.object(publish, '_gh', side_effect=gh_with(state(state='MERGED'))):
            publish.merge(Path('.'), URL, APPROVED)

    def test_unknown_cause_keeps_githubs_own_message(self):
        for info in (None, state()):
            with self.subTest(info=info is None), patch.object(publish, '_gh', side_effect=gh_with(info)):
                with self.assertRaises(BridgeError) as caught:
                    publish.merge(Path('.'), URL, APPROVED)
                self.assertEqual(caught.exception.code, 'publish_failed')
                self.assertIn('not mergeable', caught.exception.message)


if __name__ == '__main__':
    unittest.main()
