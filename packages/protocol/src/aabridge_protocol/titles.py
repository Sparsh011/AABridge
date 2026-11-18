"""Display-only task labels; full objectives remain the execution instructions."""
from __future__ import annotations

import re

MAX_BRANCH_NAME = 48
BRANCH_NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


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


def agent_branch(summary: str) -> str | None:
    """The last valid BRANCH_NAME: line. Model output, so anything else is ignored."""
    for candidate in reversed(re.findall(r'^BRANCH_NAME:[ \t]*([^\r\n]+)$', summary, re.MULTILINE)):
        name = candidate.strip().strip('`')
        if len(name) <= MAX_BRANCH_NAME and BRANCH_NAME.fullmatch(name):
            return name
    return None


def project_name(project_id: str, project: dict | None = None) -> str:
    """The name a project is shown and matched by across Macs: one folder, any number of agents.

    Older entries have no saved name; their default IDs were `project-<folder>-<agent>`.
    """
    saved = (project or {}).get('name')
    if isinstance(saved, str) and saved.strip():
        return saved.strip()
    name = project_id[len('project-'):] if project_id.startswith('project-') else project_id
    for agent in ('codex', 'claude'):
        if name.endswith('-' + agent) and len(name) > len(agent) + 1:
            return name[:-len(agent) - 1]
    return name


def project_slug(name: str) -> str:
    """Lowercase kebab-case, as the desktop groups projects with the same name."""
    slug = ''.join(c if c.isascii() and c.isalnum() else '-' for c in name.lower())
    return '-'.join(part for part in slug.split('-') if part)[:45] or 'project'
