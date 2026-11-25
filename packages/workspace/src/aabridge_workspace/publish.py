"""Deliver an accepted team result into the admin's own project checkout.

Agents never write here. AABridge copies the reviewed commit from the isolated
task clone (or the fetched review repository) and, when asked, pushes a feature
branch and opens a pull request with the admin's GitHub CLI login. A default
branch is never updated, force pushed or merged except by the admin's explicit
Merge request.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from aabridge_protocol.messages import BridgeError, require
from .git import git

PLACEMENTS = ("sandbox", "branch", "worktree", "current")
GH = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")
PROTECTED = {"main", "master", "trunk", "develop", "production", "release"}


def branch_name(agent: str, name: str | None, team_id: str) -> str:
    """`name` is an agent's validated choice; without one the admin's prompt stays out of Git."""
    return f"{agent}/{name or 'task'}-{team_id[-6:]}"


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


def refresh_baseline(repo: Path, pull: bool) -> dict:
    """Report the commit this project folder is on, after an optional fast-forward from its remote.

    Used to line two copies of a project up for a team. It only ever moves the
    checked-out branch forward to what its own upstream already has, and only
    when no tracked file has uncommitted changes. It never picks a commit,
    switches branches, merges diverged history or discards anything.
    """
    require(repo.is_dir() and git(repo, "rev-parse", "--show-toplevel") == str(repo), "The project folder is no longer a Git repository root.", "publish_blocked")
    branch = git(repo, "branch", "--show-current")
    dirty = bool(git(repo, "status", "--porcelain", "--untracked-files=no"))
    note, pulled = "", False

    def upstream() -> str | None:
        try:
            return git(repo, "rev-parse", "--verify", "--quiet", "@{upstream}")
        except BridgeError:
            return None
    if pull and branch and upstream():
        try:
            _run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "fetch", "--quiet", "--no-tags", "origin"],
                 repo, "Could not fetch from the project’s remote.", timeout=60)
        except BridgeError as exc:
            note = exc.message
        head, target = git(repo, "rev-parse", "HEAD"), upstream()
        if target and target != head and _ancestor(repo, head, target):
            if dirty:
                note = "It is behind its remote but has uncommitted changes, so it was not updated."
            else:
                git(repo, "merge", "--ff-only", "--quiet", target)
                pulled = True
    head, target = git(repo, "rev-parse", "HEAD"), upstream()
    ahead = int(git(repo, "rev-list", "--count", target + "..HEAD")) if target else 0
    return {"base_commit": head, "branch": branch, "dirty": dirty, "pulled": pulled, "ahead": ahead, "tracked": bool(target), "note": note}


LIVE = "aabridge-live/"


def live_preview(repo: Path, source: Path, base: str, name: str) -> dict:
    """Show a build in progress: point `aabridge-live/<name>` at a snapshot of the worker's files.

    The worker commits only when its turn ends. This writes the snapshot through
    a separate index, so the worker's checkout, index and branch are untouched.
    The live branch is AABridge's own and is replaced on every refresh.
    """
    import tempfile
    require(source.is_dir() and (source / ".git").is_dir() and not (source / ".git").is_symlink(), "The worker’s copy is not available on this Mac.", "publish_blocked")
    head = git(source, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory(prefix="aabridge-live-") as folder:
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(source), "LANG": "C.UTF-8", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                       "GIT_CONFIG_COUNT": "0", "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_INDEX_FILE": str(Path(folder) / "index")}

        def staged(*arguments: str) -> str:
            process = subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", str(source), *arguments],
                                     env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            require(process.returncode == 0, "Could not snapshot the work in progress.", "git_failed")
            return process.stdout.decode(errors="replace").strip()
        staged("read-tree", head)
        staged("add", "--all")
        tree = staged("write-tree")
    commit = head if tree == git(source, "rev-parse", head + "^{tree}") else git(
        source, "commit-tree", tree, "-p", head, "-m", "AABridge live preview: work in progress, not the final commit")
    git(source, "update-ref", "refs/aabridge/live", commit)
    branch = LIVE + name
    require(branch not in _worktrees(repo), f"`{branch}` is checked out in your project, so it can’t be refreshed. Switch to another branch to keep following the build.", "publish_blocked")
    git(repo, "fetch", "--quiet", "--no-tags", "--force", str(source), "+refs/aabridge/live:refs/heads/" + branch)
    return {"branch": branch, "commit": commit, "files": len(git(source, "diff", "--name-only", base, commit).splitlines())}


def drop_preview(repo: Path, name: str) -> None:
    """Remove the live branch once the real branch has the finished build."""
    branch = LIVE + name
    if _ref(repo, branch) is not None and branch not in _worktrees(repo):
        git(repo, "update-ref", "-d", "refs/heads/" + branch)


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


PULL = re.compile(r"https://([A-Za-z0-9.-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)/?\Z")
INLINE = re.compile(r"^\W*INLINE:\s*`?([^\s:`]+):(\d+)`?\s*[:\-–—]?\s*(\S.*)$", re.MULTILINE)


def inline_comments(summary: str, changed: list[str]) -> list[dict]:
    """`INLINE: path:line: text` lines from a review, kept only for files the change touched."""
    allowed, found = set(changed), []
    for path, line, text in INLINE.findall(summary or ""):
        if path in allowed and 0 < int(line) < 1_000_000 and len(found) < 20:
            found.append({"path": path, "line": int(line), "side": "RIGHT", "body": text.strip()[:4000]})
    return found


def review(repo: Path, url: str, body: str, verdict: str | None, commit: str, comments: list[dict]) -> str:
    """Post the leader's review on the pull request and say how it was posted.

    GitHub refuses an approval or a change request from the pull request's own
    author, and refuses inline comments on lines outside the diff. Each refusal
    falls back one step, ending at a plain comment, so a review is never lost.
    """
    match = PULL.match(url)
    event = {"approved": "APPROVE", "changes": "REQUEST_CHANGES"}.get(verdict or "", "COMMENT")
    attempts = [(event, comments), ("COMMENT", comments), ("COMMENT", [])] if match else []
    tried = set()
    for kind, inline in attempts:
        if (kind, bool(inline)) in tried:
            continue
        tried.add((kind, bool(inline)))
        notes = "" if inline or not comments else "\n\n" + "\n".join(f"- `{c['path']}:{c['line']}` {c['body']}" for c in comments)
        host, owner, name, number = match.groups()
        payload = {"commit_id": commit, "body": (body + notes)[:60000], "event": kind, **({"comments": inline} if inline else {})}
        try:
            _gh(repo, "api", *(("--hostname", host) if host != "github.com" else ()), "--method", "POST", f"repos/{owner}/{name}/pulls/{number}/reviews", "--input", "-",
                stdin=json.dumps(payload), failure="GitHub did not accept the review.")
            return {"APPROVE": "approval", "REQUEST_CHANGES": "change request"}.get(kind, "review") + (" with inline comments" if inline else "")
        except BridgeError:
            continue
    comment(repo, url, body)
    return "comment"


PR_FIELDS = "state,isDraft,mergeable,mergeStateStatus,reviewDecision,headRefName,headRefOid,baseRefName,number"


def pr_state(repo: Path, url: str) -> dict | None:
    try:
        return json.loads(_gh(repo, "pr", "view", url, "--json", PR_FIELDS, failure="Could not read the pull request."))
    except (BridgeError, ValueError):
        return None


def merge_blocker(info: dict, url: str, commit: str) -> tuple[str, list[str]] | None:
    """Why GitHub won't merge this pull request, and what the admin can do. None if nothing is wrong."""
    branch, base, number = info.get("headRefName", "?"), info.get("baseRefName", "?"), info.get("number", "")
    head = info.get("headRefOid") or ""
    followup = "Send the team a follow-up asking it to "
    if info.get("state") == "CLOSED":
        return (f"The pull request was closed without merging.",
                [f"Reopen it with `gh pr reopen {url}`, then press Merge again.",
                 followup + "publish the work again; it reopens or replaces the pull request."])
    if head and head != commit:
        return (f"`{branch}` now points to {head[:7]}, not the commit the leader approved ({commit[:7]}). Someone pushed to it after the review.",
                [followup + f"review the new commits on `{branch}`; once approved, press Merge again.",
                 f"If those commits shouldn't be there, put the branch back with `git push --force-with-lease origin {commit}:refs/heads/{branch}`, then press Merge again."])
    if info.get("isDraft"):
        return ("The pull request is still a draft.", [f"Mark it ready with `gh pr ready {url}`, then press Merge again."])
    status, mergeable = info.get("mergeStateStatus"), info.get("mergeable")
    if mergeable == "CONFLICTING" or status == "DIRTY":
        return (f"`{branch}` conflicts with `{base}`: `{base}` changed the same lines after this task started, so GitHub can't merge it automatically.",
                [followup + f"merge `{base}` into `{branch}` and resolve the conflicts. The leader reviews the result, then press Merge again.",
                 f"Or resolve it yourself: `gh pr checkout {number} && git fetch origin {base} && git merge origin/{base}`, fix the conflicts, commit and push. "
                 "Then send a follow-up so the leader reviews that commit, because AABridge only merges the commit the leader approved."])
    if status == "BEHIND":
        return (f"`{branch}` is behind `{base}`, and this repository requires branches to be up to date before merging.",
                [followup + f"update `{branch}` from `{base}`; the leader reviews it, then press Merge again.",
                 f"Or run `gh pr update-branch {url}`, then send a follow-up so the leader reviews the updated commit."])
    if status == "BLOCKED":
        if info.get("reviewDecision") in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}:
            return ("Branch protection on `" + base + "` requires an approving review from someone other than the pull request's author" +
                    (", and a reviewer requested changes." if info.get("reviewDecision") == "CHANGES_REQUESTED" else "."),
                    [f"Have a collaborator approve {url}, then press Merge again.",
                     f"Or, if you're the only maintainer, relax the review rule for `{base}` in the repository's Settings → Branches (or Rules)."])
        return (f"Branch protection on `{base}` is blocking the merge, usually a required status check that hasn't passed yet.",
                [f"See which check is failing with `gh pr checks {url}`.",
                 followup + "fix the failing check; the leader reviews it, then press Merge again.",
                 "If the checks are still running, wait for them, then press Merge again."])
    if mergeable == "UNKNOWN" or status == "UNKNOWN":
        return ("GitHub is still working out whether the pull request can be merged.", ["Wait a minute, then press Merge again."])
    return None


def merge(repo: Path, url: str, commit: str) -> None:
    try:
        _gh(repo, "pr", "merge", url, "--merge", "--match-head-commit", commit, failure="GitHub did not merge the pull request.")
    except BridgeError as exc:
        info = pr_state(repo, url)
        if info and info.get("state") == "MERGED" and info.get("headRefOid") == commit:
            return  # Already merged, e.g. by hand or by an earlier attempt.
        found = merge_blocker(info, url, commit) if info else None
        if not found:
            raise
        reason, fixes = found
        raise BridgeError("merge_blocked", reason + "\nHow to fix:\n" + "\n".join(f"{n}. {fix}" for n, fix in enumerate(fixes, 1))) from None
