"""Bounded Git evidence for a review, independent of either Mac's paths/remotes."""
from __future__ import annotations

import base64
import tempfile
import uuid
from pathlib import Path

from aabridge_protocol.messages import require
from .git import git

MAX_BUNDLE = 60000  # Base64 fits alongside review instructions in a 128 KiB RPC.


def export_snapshot(source: Path, base: str, commit: str) -> dict:
    require(git(source, 'rev-parse', '--verify', commit + '^{commit}') == commit,
            'The implementation commit is unavailable for review.', 'review_unavailable')
    git(source, 'merge-base', '--is-ancestor', base, commit)
    if base == commit:
        return {'commit': commit, 'bundle': ''}
    # Bundle creation needs an advertised ref. Never move a task branch.
    ref = 'refs/aabridge-review/' + uuid.uuid4().hex
    git(source, 'update-ref', ref, commit)
    try:
        with tempfile.TemporaryDirectory(prefix='aabridge-review-') as folder:
            bundle = Path(folder) / 'review.bundle'
            git(source, 'bundle', 'create', str(bundle), ref, '^' + base)
            require(bundle.stat().st_size <= MAX_BUNDLE,
                    'The implementation Git bundle exceeds the automatic review limit (60,000 bytes). Review it manually; no leader turn was started.', 'review_too_large')
            return {'commit': commit, 'bundle': base64.b64encode(bundle.read_bytes()).decode('ascii')}
    finally:
        git(source, 'update-ref', '-d', ref, commit)


def import_snapshot(workspace: Path, base: str, snapshot: dict, branch: str) -> None:
    """Verify evidence before moving this isolated review checkout to its exact head."""
    require(not git(workspace, 'status', '--porcelain'),
            'The review checkout has unexpected edits; it was preserved.', 'review_unavailable')
    commit = snapshot['commit']
    if snapshot['bundle']:
        with tempfile.TemporaryDirectory(prefix='aabridge-review-') as folder:
            bundle = Path(folder) / 'review.bundle'
            bundle.write_bytes(base64.b64decode(snapshot['bundle'], validate=True))
            git(workspace, 'bundle', 'verify', str(bundle))
            git(workspace, 'fetch', '--no-tags', str(bundle), commit)
            require(git(workspace, 'rev-parse', 'FETCH_HEAD') == commit,
                    'Review bundle commit identity mismatch.', 'review_unavailable')
    require(git(workspace, 'rev-parse', '--verify', commit + '^{commit}') == commit,
            'Review commit identity mismatch.', 'review_unavailable')
    git(workspace, 'merge-base', '--is-ancestor', base, commit)
    git(workspace, 'checkout', '-B', branch, commit)
