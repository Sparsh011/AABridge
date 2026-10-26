"""Check or push an agent branch without overwriting another agent's work.

Usage: python scripts/git_sync.py [--push] [--offline]
Online checks fetch first. Push requires a clean feature branch containing the
latest origin/main and origin/<branch>. A racing push is rejected by Git.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]

def run(*args, check=True):
    options = (['-c', 'url.https://github.com/.insteadOf=git@github.com:', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential'] if args and args[0] in {'fetch', 'push'} else [])
    result = subprocess.run(['git', *options, *args], cwd=REPOSITORY, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Git check failed.')
    return result


def inspect(*, offline=False):
    branch = run('branch', '--show-current').stdout.strip()
    if not branch or branch in {'main', 'master'}:
        raise RuntimeError('Use your own feature branch or worktree. Direct main pushes are disabled by this helper.')
    if run('status', '--porcelain').stdout.strip():
        raise RuntimeError('Commit your own changes first. Leave another agent’s changes untouched.')
    if not offline:
        run('fetch', '--prune', 'origin')
    run('rev-parse', '--verify', 'refs/remotes/origin/main')
    if run('merge-base', '--is-ancestor', 'origin/main', 'HEAD', check=False).returncode:
        raise RuntimeError('Your branch does not contain the latest origin/main. Integrate it in your own worktree, resolve conflicts, and rerun checks.')
    remote_branch = 'refs/remotes/origin/' + branch
    if run('show-ref', '--verify', '--quiet', remote_branch, check=False).returncode == 0:
        if run('merge-base', '--is-ancestor', remote_branch, 'HEAD', check=False).returncode:
            raise RuntimeError('GitHub has commits missing from your branch. Integrate them before pushing; never force push shared work.')
    return {'branch': branch, 'head': run('rev-parse', 'HEAD').stdout.strip(), 'remote_checked': not offline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--push', action='store_true')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    try:
        if args.push and args.offline:
            raise RuntimeError('A push requires a fresh remote check. Remove --offline.')
        report = inspect(offline=args.offline)
        if args.push:
            # An ordinary non-force push protects against concurrent branch updates.
            run('push', '--set-upstream', 'origin', 'HEAD:refs/heads/' + report['branch'])
            report['pushed'] = True
        print(json.dumps(report, indent=2))
        return 0
    except RuntimeError as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
