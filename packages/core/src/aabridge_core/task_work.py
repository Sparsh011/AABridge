"""Inspect and discard only a stopped task's isolated workspace copies."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path

from aabridge_protocol.messages import BridgeError, canonical, require
from aabridge_protocol.titles import compact_title
from aabridge_workspace.git import git as workspace_git
from .config import private_directory, write_private

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
MAX_ENTRIES = 30000


def git(path, *arguments):
    return workspace_git(path, '--no-optional-locks', *arguments)


def _roots(state, task):
    parent = private_directory(state / 'workspaces', create=True)
    require(parent.resolve() == parent, 'The workspace folder changed; no files were removed.', 'unsafe_workspace')
    roots = [parent / task['id'], parent / (task['id'] + '.linked')]
    for root in roots:
        require(not root.is_symlink(), 'A task folder is a symbolic link; no files were removed.', 'unsafe_workspace')
        if root.exists():
            require(root.is_dir() and root.resolve() == root and root.stat().st_uid == os.getuid(),
                    'A task folder has unexpected ownership or location.', 'unsafe_workspace')
    return [root for root in roots if root.exists()]


def _inventory(roots):
    records, total = [], 0
    for root in roots:
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            require(len(records) < MAX_ENTRIES, 'This workspace is too large for a complete deletion preview. No files were removed.', 'work_review_too_large')
            records.append((str(path), info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns))
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
            elif stat.S_ISDIR(info.st_mode):
                pending.extend(sorted(path.iterdir(), reverse=True))
    return records, total


def _team_usage(store, task_id):
    usage = []
    for (raw,) in store.db.execute('SELECT document FROM teams'):
        room = json.loads(raw)
        stages = [item for item in room.get('stage_tasks', []) if item['task_id'] == task_id and item['target'] == 'local']
        if stages:
            usage.append({'title': room.get('title') or compact_title(room['objective']), 'status': room['status'],
                          'active': room['status'] not in {'completed', 'cancelled', 'failed'},
                          'stages': sorted({item['stage'] for item in stages})})
    return usage


def _repositories(state, task, project):
    paths = [('Main project', state / 'workspaces' / task['id'], task['base_commit'])]
    parent = state / 'workspaces' / (task['id'] + '.linked')
    if parent.exists():
        manifest = parent / 'manifest.json'
        require(manifest.is_file() and not manifest.is_symlink(), 'The linked-workspace manifest is unavailable.', 'unsafe_workspace')
        require(manifest.stat().st_size <= 16000, 'Linked-workspace metadata exceeds the review limit.', 'unsafe_workspace')
        bases = json.loads(manifest.read_text())
        # Enumerate the saved task copies, including links removed from project settings later.
        for name, base in bases.items():
            from aabridge_workspace.linked import NAME
            from aabridge_protocol.messages import SHA
            require(NAME.fullmatch(name) and isinstance(base, str) and SHA.fullmatch(base), 'Invalid linked-workspace metadata.', 'unsafe_workspace')
            paths.append((name, parent / name, base))
    return [(name, path, base) for name, path, base in paths if path.exists()]


def _snapshot(task, usage, inventory):
    return hashlib.sha256(canonical({'task': task['id'], 'revision': task['revision'], 'status': task['status'],
                                    'worker_pid': task.get('worker_pid'), 'usage': usage, 'files': inventory}).encode()).hexdigest()


def preview(store, task, project, identity):
    state = store.state
    roots = _roots(state, task)
    inventory, size = _inventory(roots)
    usage = _team_usage(store, task['id'])
    blockers = []
    if identity != 'local' and task['sender'] != identity:
        blockers.append('This shared task is read-only. Its owner can delete its saved work.')
    if task['status'] not in TERMINAL or task.get('worker_pid') is not None:
        blockers.append('Work is still running or closing. Stop it and refresh this review before deleting files.')
    if any(item['active'] for item in usage):
        blockers.append('An active team uses this work. Stop that team before deleting its saved work.')
    repositories, remaining, changed = [], 18000, 0
    for name, path, base in _repositories(state, task, project):
        require(not path.is_symlink() and path.resolve() == path and (path / '.git').is_dir() and not (path / '.git').is_symlink(),
                'A task copy is not an isolated Git clone. No files were removed.', 'unsafe_workspace')
        require(not (path / '.git' / 'objects' / 'info' / 'alternates').exists(), 'This task copy shares Git objects; delete it manually after checking other work.', 'unsafe_workspace')
        require(git(path, 'rev-parse', '--show-toplevel') == str(path), 'The task Git checkout points outside its folder.', 'unsafe_workspace')
        head = git(path, 'rev-parse', '--verify', 'HEAD')
        saved = git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z', base, head).split('\0')
        pending = git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z', 'HEAD').split('\0')
        untracked = git(path, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
        names = sorted(set(saved + pending + untracked) - {''})
        changed += len(names)
        files = []
        for relative in names[:200]:
            # Git paths are labels only. No user-supplied filesystem path is accepted by deletion.
            kind = 'New file' if relative in untracked else 'Saved + uncommitted changes' if relative in pending and relative in saved else 'Uncommitted change' if relative in pending else 'Saved in task commit'
            excerpt = ''
            if relative not in untracked and remaining > 0 and len(files) < 20:
                full_diff = git(path, 'diff', '--no-ext-diff', '--no-textconv', base, '--', relative)
                excerpt = full_diff[:min(4000, remaining)]
                if len(excerpt) < len(full_diff):
                    excerpt += '\n[Text preview shortened]'
                remaining -= len(excerpt)
            elif relative in untracked and remaining > 0:
                file = path / relative
                # Do not follow any symlink component to read a new file's contents.
                parts = [file, *file.parents]
                safe = all(not item.is_symlink() for item in parts if item.is_relative_to(path))
                if safe and file.is_file() and file.stat().st_size <= 32000:
                    with file.open('rb') as stream:
                        data = stream.read(min(4000, remaining))
                    if b'\0' not in data:
                        excerpt = data.decode('utf-8', errors='replace')
                        if len(data) < file.stat().st_size:
                            excerpt += '\n[Text preview shortened]'
                        remaining -= len(excerpt)
            files.append({'path': relative, 'kind': kind, 'preview': excerpt})
        if len(names) > 200:
            blockers.append(f'{name} has more than 200 changed files. Review and clean up this copy manually.')
        repositories.append({'name': name, 'path': str(path), 'branch': git(path, 'branch', '--show-current'),
                             'head': head, 'saved_commit_count': int(git(path, 'rev-list', '--count', 'HEAD', '--all', '^' + base)),
                             'changed_files': len(names), 'files': files})
    # Reject even a completed preview if source files changed during inspection.
    if task['status'] in TERMINAL and task.get('worker_pid') is None:
        require(_inventory(roots)[0] == inventory, 'The work changed while it was inspected. Refresh the review.', 'stale_work_review')
    fingerprint = _snapshot(task, usage, inventory)
    result = {'task_id': task['id'], 'status': task['status'], 'snapshot': fingerprint, 'bytes': size,
            'changed_files': changed, 'repositories': repositories, 'team_usage': usage, 'blockers': blockers,
            'can_delete_work': bool(roots) and not blockers and not task.get('work_deleted'),
            'work_deleted': bool(task.get('work_deleted')), 'cleanup_pending': bool(task.get('work_cleanup')),
            'running': task['status'] not in TERMINAL or task.get('worker_pid') is not None,
            'can_stop': (identity == 'local' or task['sender'] == identity) and task['status'] not in TERMINAL,
            'remote_team_possible': task['sender'] != 'local',
            'kept': ['Original project folders', 'Published branches and pull requests', 'Saved conversation and team records']}
    require(len(canonical(result).encode()) <= 180000, 'The complete work preview exceeds the response limit. No files were removed.', 'work_review_too_large')
    return result


def stage(store, task, project, identity, snapshot):
    if task.get('work_deleted'):
        return
    review = preview(store, task, project, identity)
    require(review['can_delete_work'], '\n'.join(review['blockers']) or 'There is no task workspace to delete.', 'work_delete_blocked')
    require(review['snapshot'] == snapshot, 'The work changed after your preview. Refresh and review it again.', 'stale_work_review')
    require(shutil.rmtree.avoids_symlink_attacks, 'This Python cannot safely clean task folders. No files were removed.', 'work_delete_blocked')
    parent = private_directory(store.state / 'deleted-work', create=True)
    quarantine = parent / (task['id'] + '-' + uuid.uuid4().hex)
    quarantine.mkdir(mode=0o700)
    store._staged_work = (quarantine, [])
    roots = _roots(store.state, task)
    write_private(quarantine / 'manifest.json', canonical({'task_id': task['id'], 'roots': [root.name for root in roots]}).encode(), exclusive=True)
    for directory in (quarantine, parent):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    original_inventory = _inventory(roots)[0]
    require(_snapshot(task, _team_usage(store, task['id']), original_inventory) == snapshot,
            'The work changed after your preview. Refresh and review it again.', 'stale_work_review')
    for root in roots:
        destination = quarantine / root.name
        os.rename(root, destination)
        store._staged_work[1].append((root, destination))
    moved_inventory = _inventory([destination for _, destination in store._staged_work[1]])[0]
    normalised = []
    for record in moved_inventory:
        original = next(source / Path(record[0]).relative_to(destination) for source, destination in store._staged_work[1] if Path(record[0]).is_relative_to(destination))
        normalised.append((str(original), *record[1:]))
    require(normalised == original_inventory, 'The work changed during deletion. It was preserved; review it again.', 'stale_work_review')
    for directory in (quarantine, store.state / 'workspaces'):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    task.update(work_deleted=True, work_cleanup=quarantine.name)
    store.save(task, 'work_deleted')
    store.append_message(task, 'system', 'work_deleted', 'The isolated workspace copies were deleted after a user reviewed the saved work. Original projects and published branches were kept.')


def rollback(store):
    pending = store._staged_work
    if pending:
        quarantine, moves = pending
        for original, staged in reversed(moves):
            require(not original.exists() and not original.is_symlink(), f'Could not restore {original}; the saved work is preserved in {quarantine}.', 'work_restore_blocked')
            os.rename(staged, original)
        (quarantine / 'manifest.json').unlink(missing_ok=True)
        quarantine.rmdir()
    store._staged_work = None


def finish(store, task):
    name = task.get('work_cleanup')
    if not name:
        return False
    require(isinstance(name, str) and name.startswith(task['id'] + '-') and '/' not in name and '..' not in name,
            'Unexpected cleanup location; no files were removed.', 'unsafe_workspace')
    parent = private_directory(store.state / 'deleted-work')
    path = parent / name
    require(not path.is_symlink(), 'Cleanup folder changed; no files were removed.', 'unsafe_workspace')
    try:
        if path.exists():
            require(path.resolve() == path, 'Unexpected cleanup location.', 'unsafe_workspace')
            shutil.rmtree(path)
        store.update(task['id'], 'work_cleanup_finished', work_cleanup=None)
        return False
    except OSError:
        return True  # Remains durably staged; the same confirmed request can retry cleanup.


def reconcile(store):
    """Recover an interrupted move when SQLite did not commit its deletion."""
    parent = store.state / 'deleted-work'
    if not parent.exists():
        return
    private_directory(parent)
    for quarantine in parent.iterdir():
        require(quarantine.is_dir() and not quarantine.is_symlink(), 'An unfinished cleanup folder needs local inspection; no files were removed.', 'unsafe_workspace')
        manifest = quarantine / 'manifest.json'
        if not manifest.exists():
            continue  # An interruption before the journal was written moved no work.
        require(not manifest.is_symlink() and manifest.is_file() and manifest.stat().st_size <= 8192,
                'An unfinished work journal needs local inspection; all files were preserved.', 'unsafe_workspace')
        value = json.loads(manifest.read_text())
        from aabridge_protocol.messages import identifier
        task_id = identifier(value['task_id'])
        suffix = quarantine.name.removeprefix(task_id + '-')
        require(quarantine.name.startswith(task_id + '-') and len(suffix) == 32 and all(c in '0123456789abcdef' for c in suffix),
                'Unexpected unfinished-work folder; all files were preserved.', 'unsafe_workspace')
        roots = value['roots']
        require(isinstance(roots, list) and set(roots) <= {task_id, task_id + '.linked'} and len(roots) == len(set(roots)),
                'Unexpected unfinished-work roots; all files were preserved.', 'unsafe_workspace')
        row = store.db.execute('SELECT document FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row and json.loads(row[0]).get('work_deleted'):
            continue  # Committed cleanup remains available for an authorized retry.
        workspaces = private_directory(store.state / 'workspaces', create=True)
        for name in roots:
            staged, original = quarantine / name, workspaces / name
            require(not staged.is_symlink(), 'Unfinished work changed location; all files were preserved.', 'unsafe_workspace')
            if staged.exists():
                require(not original.exists() and not original.is_symlink(),
                        f'Unfinished work is preserved in {quarantine}. The original task location is occupied; inspect both copies locally.', 'work_restore_blocked')
                os.rename(staged, original)
        manifest.unlink()
        quarantine.rmdir()
