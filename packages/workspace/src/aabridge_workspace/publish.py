"""Deliver an accepted team result into the admin's own project checkout.

Agents never write here. AABridge copies the reviewed commit from the isolated
task clone (or the fetched review repository) and, when asked, pushes a feature
branch and opens a pull request with the admin's GitHub CLI login. A default
branch is never updated, force pushed or merged except by the admin's explicit
Merge request.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from aabridge_protocol.messages import BridgeError, require
from .git import git

PLACEMENTS = ("sandbox", "branch", "worktree", "current")
GH = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")
PROTECTED = {"main", "master", "trunk", "develop", "production", "release"}


def slug(text: str, limit: int = 40) -> str:
    words: list[str] = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len("-".join([*words, word])) > limit:
            break
        words.append(word)
    return "-".join(words) or "task"


def branch_name(agent: str, objective: str, team_id: str) -> str:
    return f"{agent}/{slug(objective)}-{team_id[-6:]}"


def _ancestor(repo: Path, older: str, newer: str) -> bool:
    try:
        git(repo, "merge-base", "--is-ancestor", older, newer)
        return True
    except BridgeError:
        return False


def _ref(repo: Path, name: str) -> str | None:
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/" + name)
    except BridgeError:
        return None


def _worktrees(repo: Path) -> dict[str, Path]:
    found, path = {}, None
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[9:])
        elif line.startswith("branch refs/heads/") and path is not None:
            found[line[18:]] = path
    return found


def _clean(path: Path) -> None:
    require(not git(path, "status", "--porcelain"), f"{path} has uncommitted changes. Commit or stash them, then retry.", "publish_blocked")


def _environment() -> dict[str, str]:
    home = str(Path.home())
    return {"HOME": home, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0", "GH_PROMPT_DISABLED": "1", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}


def _run(command: list[str], cwd: Path, failure: str, stdin: str | None = None, timeout: int = 90) -> str:
    try:
        process = subprocess.run(command, cwd=cwd, env=_environment(), input=(stdin or "").encode(),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise BridgeError("publish_failed", failure + " The GitHub CLI (gh) is not installed. Install it with `brew install gh`, then run `gh auth login`.") from None
    except (OSError, subprocess.TimeoutExpired):
        raise BridgeError("publish_failed", failure + " The command timed out.") from None
    if process.returncode:
        detail = process.stderr.decode(errors="replace").strip().splitlines()
        raise BridgeError("publish_failed", failure + (" " + detail[-1][:400] if detail else ""))
    return process.stdout.decode(errors="replace").strip()


def _gh(repo: Path, *arguments: str, stdin: str | None = None, failure: str) -> str:
    gh = next((p for p in GH if os.path.exists(p)), "gh")
    return _run([gh, *arguments], repo, failure, stdin)


def default_branch(repo: Path, *, online: bool = True) -> str:
    try:
        return git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix("origin/")
    except BridgeError:
        if not online:
            return "main"
        return _gh(repo, "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name",
                   failure="Could not find the default branch on GitHub.")


def place(repo: Path, source: Path, commit: str, mode: str, branch: str, worktree_root: Path | None = None) -> dict:
    """Make `commit` available in the admin's repository without touching main."""
    require(mode in PLACEMENTS and mode != "sandbox", "Unknown result placement.")
    require(repo.is_dir() and git(repo, "rev-parse", "--show-toplevel") == str(repo), "The project folder is no longer a Git repository root.", "publish_blocked")
    git(repo, "fetch", "--quiet", "--no-tags", str(source), commit)
    git(repo, "rev-parse", "--verify", commit + "^{commit}")
    if mode == "current":
        current = git(repo, "branch", "--show-current")
        require(current, "Your project is not on a branch (detached HEAD). Check out a feature branch or choose New branch.", "publish_blocked")
        protected = PROTECTED | {default_branch(repo, online=False)}
        require(current not in protected, f"Your project is on `{current}`. AABridge never commits to a main branch; choose New branch or check out a feature branch.", "publish_blocked")
        head = git(repo, "rev-parse", "HEAD")
        if head != commit:
            _clean(repo)
            require(_ancestor(repo, head, commit), f"`{current}` moved since the task started, so the work can't be added cleanly. Choose New branch instead.", "publish_blocked")
            git(repo, "merge", "--ff-only", "--quiet", commit)
        return {"mode": mode, "branch": current, "path": str(repo), "commit": commit}
    checked_out = _worktrees(repo)
    existing = _ref(repo, branch)
    if existing and existing != commit:
        require(_ancestor(repo, existing, commit), f"Branch `{branch}` has commits that aren't part of this task. Rename or delete it, then retry.", "publish_blocked")
        if branch in checked_out:
            _clean(checked_out[branch])
            git(checked_out[branch], "merge", "--ff-only", "--quiet", commit)
        else:
            git(repo, "update-ref", "refs/heads/" + branch, commit, existing)
    elif not existing:
        git(repo, "branch", "--no-track", branch, commit)
    path = checked_out.get(branch)
    if mode == "worktree" and path is None:
        root = worktree_root or repo.parent / (repo.name + "-worktrees")
        path = root / branch.replace("/", "-")
        require(not path.exists(), f"{path} already exists. Move it away, then retry.", "publish_blocked")
        root.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--quiet", str(path), branch)
    return {"mode": mode, "branch": branch, "path": str(path if mode == "worktree" else repo), "commit": commit}


def free_branch(repo: Path, branch: str) -> str:
    """A new placement never adopts a branch somebody else already made."""
    candidate, number = branch, 2
    while _ref(repo, candidate) is not None:
        candidate, number = f"{branch}-{number}", number + 1
    return candidate


def pull_request(repo: Path, branch: str, title: str, body: str, existing: str | None = None) -> dict:
    """Push the feature branch (never forced) and open or reuse its pull request."""
    try:
        git(repo, "remote", "get-url", "origin")
    except BridgeError:
        raise BridgeError("publish_failed", f"This project has no `origin` remote, so no pull request was opened. Branch `{branch}` is ready in your project.") from None
    base = default_branch(repo)
    require(branch != base and branch not in PROTECTED, "A pull request needs a feature branch, not the main branch.", "publish_blocked")
    _run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "url.https://github.com/.insteadOf=git@github.com:", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
          "push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], repo,
         f"Could not push `{branch}` to GitHub. Check `gh auth status`.")
    url = existing
    if not url:
        found = _gh(repo, "pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url // empty",
                    failure="Could not check GitHub for an existing pull request.")
        url = found or _gh(repo, "pr", "create", "--head", branch, "--base", base, "--title", title[:200], "--body-file", "-",
                           stdin=body, failure="Could not open the pull request.").splitlines()[-1]
    require(url.startswith("https://"), "GitHub did not return a pull request link.", "publish_failed")
    return {"url": url, "base": base}


def comment(repo: Path, url: str, body: str) -> None:
    _gh(repo, "pr", "comment", url, "--body-file", "-", stdin=body[:60000], failure="Could not post the review on the pull request.")


def merge(repo: Path, url: str, commit: str) -> None:
    _gh(repo, "pr", "merge", url, "--merge", "--match-head-commit", commit,
        failure="GitHub did not merge the pull request. If its head changed, review and publish that revision before retrying.")
