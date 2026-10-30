"""Linked projects: other repositories on this Mac that a task may read or edit.

A project's local owner lists related folders (for example a server's Android
and Mac apps) with `read` or `edit` access. Each task gets its own isolated
clone of every linked repository next to the main task checkout, on the same
task branch, so agents keep the whole system in view without touching the
admin's folders. Edits to `edit` links are committed and reported like the
main project; anything changed in a `read` link is discarded after the turn.
Agents are told the access level, and the CLIs receive only `edit` folders as
writable (Codex) or all linked folders as extra directories (Claude Code).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from aabridge_protocol.messages import BridgeError, fields, require
from .git import git

ACCESS = ("read", "edit")
MAX_LINKS = 8
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")


def link_name(repository: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", repository.name.lower()).strip("-")[:40].strip("-")
    return slug or "project"


def validate_links(state: Path, repository: Path, links: object) -> list[dict]:
    """Check the owner's link list when a project is saved."""
    require(isinstance(links, list) and len(links) <= MAX_LINKS, f"Link up to {MAX_LINKS} related projects.")
    result, names, folders = [], set(), {repository}
    for link in links:
        fields(link, {"repository", "access"}, {"name"})
        require(link["access"] in ACCESS, "Choose Can read or Can edit for each related project.")
        require(isinstance(link["repository"], str) and link["repository"].startswith("/"), "Choose a related project folder.")
        folder = Path(link["repository"]).expanduser().resolve(strict=True)
        require(folder.is_dir() and not folder.is_relative_to(state), "Choose a related project folder outside AABridge’s own data.")
        try:
            top = git(folder, "rev-parse", "--show-toplevel")
            git(folder, "rev-parse", "--verify", "HEAD^{commit}")
        except BridgeError:
            raise BridgeError("invalid_request", f"{folder.name} isn’t a Git project with at least one commit. Run git init and commit, then link it.") from None
        require(top == str(folder), f"Choose the top folder of the {folder.name} Git project.")
        require(folder not in folders, f"{folder.name} is already linked or is this project itself.")
        name = link.get("name") or link_name(folder)
        require(isinstance(name, str) and NAME.fullmatch(name) is not None, "Use lowercase letters, numbers and dashes for a related project name.")
        require(name not in names, f"Two related projects are both named {name}. Rename one of the folders or the link.")
        names.add(name); folders.add(folder)
        result.append({"name": name, "repository": str(folder), "access": link["access"]})
    return result


def root(state: Path, task_id: str) -> Path:
    return state / "workspaces" / (task_id + ".linked")


def paths(state: Path, task: dict, project: dict) -> list[tuple[dict, Path]]:
    folder = root(state, task["id"])
    return [(link, folder / link["name"]) for link in project.get("links", [])]


def prepare(state: Path, task: dict, project: dict, branch: str) -> None:
    """Clone each linked repository once per task at its current commit."""
    links = paths(state, task, project)
    if not links:
        return
    folder = root(state, task["id"])
    manifest = folder / "manifest.json"
    bases = json.loads(manifest.read_text()) if manifest.is_file() else {}
    folder.mkdir(mode=0o700, exist_ok=True)
    for link, path in links:
        if path.exists():
            continue
        source = Path(link["repository"])
        require(source.is_dir(), f"The related project {link['name']} ({source}) is missing. Reconnect its drive or update the project’s links in Projects.", "linked_unavailable")
        git(folder, "clone", "--quiet", "--no-local", "--no-hardlinks", "--template=", "--", str(source), str(path))
        git(path, "checkout", "--quiet", "-B", branch)
        git(path, "remote", "remove", "origin")  # Never push back into the admin's folder.
        bases[link["name"]] = git(path, "rev-parse", "HEAD")
    manifest.write_text(json.dumps(bases))


def describe(state: Path, task: dict, project: dict) -> str:
    """The agent's map of linked projects, read by the native worker prompt."""
    links = paths(state, task, project)
    if not links:
        return ""
    lines = [f"- {path} ({link['name']}): " + ("you may edit it; your changes are kept on this task branch." if link["access"] == "edit"
             else "read only. Do not edit it; changes there are discarded.") for link, path in links]
    return ("Linked projects are part of this task. Use them to keep the projects consistent with each other; "
            "check how they call one another before you change a shared contract.\n" + "\n".join(lines) + "\n")


def results(state: Path, task: dict, project: dict, branch: str, edits_allowed: bool) -> tuple[list[dict], list[str]]:
    """Commit edit links, discard changes to read links, and report each diff."""
    links = paths(state, task, project)
    if not links:
        return [], []
    bases = json.loads((root(state, task["id"]) / "manifest.json").read_text())
    output, notes = [], []
    for link, path in links:
        base = bases[link["name"]]
        dirty = bool(git(path, "status", "--porcelain"))
        if link["access"] == "edit" and edits_allowed:
            if dirty:
                git(path, "add", "--all")
                git(path, "commit", "--quiet", "-m", f"AABridge {task['id']} revision {task['revision']} ({link['name']})")
        else:
            head = git(path, "rev-parse", "HEAD")
            if dirty or head != base:
                git(path, "reset", "--quiet", "--hard", base)
                git(path, "clean", "-fdq")
                notes.append(f"Changes to {link['name']} were discarded because this step may only read it.")
        commit = git(path, "rev-parse", "HEAD")
        diff = git(path, "diff", "--no-ext-diff", "--no-textconv", base, commit) if commit != base else ""
        output.append({"name": link["name"], "access": link["access"], "repository": link["repository"], "worktree": str(path),
                       "branch": branch, "base_commit": base, "result_commit": commit,
                       "changed_paths": git(path, "diff", "--name-only", base, commit).splitlines() if commit != base else [],
                       "diff": diff[:64000], "diff_truncated": len(diff) > 64000})
    return output, notes
