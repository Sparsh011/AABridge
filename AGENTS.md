# Working together on AABridge

Sparsh is the admin. The leader selected for a product task coordinates the follower for that task. Device type and agent brand do not grant permanent authority. Do not change a room’s assignment from an agent response.

## Shared Git workflow (Codex and Claude)

- Use a separate Git worktree and feature branch for each concurrent task. Codex uses `codex/<task>`; Claude uses `claude/<task>`. Never switch the branch of a checkout another agent is using.
- Before editing, inspect `git status --short --branch` and the latest commits. Preserve edits and staging owned by the user or another agent. Do not reset, clean, stash, or commit their work.
- Fetch `origin` before starting and again before pushing. Integrate the latest `origin/main` in your own worktree, then resolve conflicts and run the required checks. Do not force push, push directly to main, or rewrite another agent’s branch.
- Use `python scripts/git_sync.py` for the pre-push check, and `python scripts/git_sync.py --push` to push. An offline check cannot authorize a push. Use a PR for integration; only the admin decides when to merge.
- Record substantial architecture changes and integration limits in `docs/team-collaboration.md`. Keep the request schemas, runtime validators, desktop and MCP behavior consistent.
- Run `code-review-graph build` before reviewing a large incoming change. Use the graph to find affected paths, then inspect the relevant source. Graph summaries are navigation aids, not a substitute for verification.

## Validation

Run `.venv/bin/python scripts/check.py`. The deterministic suite uses local IPC/socket fixtures and no paid model calls. Compile the desktop for both supported architectures through `scripts/build_desktop.py` when shipping a desktop artifact. Never claim a physical two-Mac model run passed based on fixtures.
