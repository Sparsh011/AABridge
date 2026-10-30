"""Select existing local runtimes without installing or executing an agent."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from aabridge_core.config import load_config
from aabridge_preflight.paths import default_state_directory
from aabridge_preflight.runtimes import AGENTS, runtime_report


def select(paths, *, interactive):
    selected = {}
    for agent in AGENTS:
        path = paths.get(agent)
        report = runtime_report(agent, path)
        while report['status'] != 'found' and interactive:
            print(report['message'])
            try:
                path = input(f'{agent}: enter an existing app/executable path, or Enter to skip: ').strip()
            except EOFError:
                path = ''
            if not path:
                break
            # Terminal paths are text, never evaluated as shell commands.
            if len(path) >= 2 and path[0] == path[-1] and path[0] in {'"', "'"}:
                path = path[1:-1]
            report = runtime_report(agent, path)
        if report['status'] == 'found':
            selected[agent] = path or report['executable']
            print(f'{agent}: {report["executable"]}')
        else:
            print(f'{agent}: not selected. Choose its location in Set up this Mac before using it.')
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    for agent in AGENTS:
        parser.add_argument('--' + agent + '-path')
    args = parser.parse_args()
    paths = {}
    state = default_state_directory()
    if (state / 'config.json').exists():
        paths.update({agent: value.get('path') for agent, value in load_config(state).get('runtimes', {}).items()})
    if args.output.exists():
        saved = json.loads(args.output.read_text())
        paths.update({agent: saved[agent] for agent in AGENTS if agent in saved})
    for agent in AGENTS:
        explicit = getattr(args, agent + '_path')
        if explicit:
            report = runtime_report(agent, explicit)
            if report['status'] != 'found':
                raise SystemExit(report['message'])
            paths[agent] = explicit
    selected = select(paths, interactive=sys.stdin.isatty())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'version': 1, **selected}, indent=2) + '\n')
    args.output.chmod(0o600)


if __name__ == '__main__':
    main()
