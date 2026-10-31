"""Display-only task labels; full objectives remain the execution instructions."""
from __future__ import annotations

import re


def compact_title(objective: str) -> str:
    original = objective.rsplit('Admin task:', 1)[-1].strip()
    if original.startswith(('You are the follower.', 'Your leader reviewed your work')):
        return 'Implementation task'
    first = next((line.strip() for line in original.splitlines() if line.strip()), '')
    first = re.split(r'[.!?](?:\s|$)', first, maxsplit=1)[0]
    label = ' '.join(first.split()[:8])
    if len(label) > 80:
        label = label[:80].rsplit(' ', 1)[0] if ' ' in label[:80] else label[:80]
    return label.rstrip(' ,;:-') or 'Untitled task'


def leader_title(summary: str, objective: str) -> str:
    from .messages import BridgeError, task_title
    for candidate in reversed(re.findall(r'^TASK_TITLE:[ \t]*([^\r\n]+)$', summary, re.MULTILINE)):
        try:
            return task_title(candidate.strip())
        except BridgeError:
            pass
    return compact_title(objective)
