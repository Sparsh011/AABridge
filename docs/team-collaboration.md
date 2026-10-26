# Team collaboration preview · 0.3.0.dev1

AABridge now treats every Mac as a peer. A room has a human admin, a leader chosen by that admin, and a follower chosen by that admin. Either Codex or Claude on either Mac can lead. The assignment belongs to the room and does not change global device authority.

## Use it

1. Install the same AABridge Team build on both Macs. In Settings choose the installed agent and select **Set up this Mac**. Full coding setup does not require a demo or a special Claude token. Sign in to the agent CLI on its own Mac.
2. Turn on Remote Login in macOS Settings on both Macs. In **Devices**, create and copy each Mac’s public connection card to the other Mac, then connect it. Exchange both cards. A card pins the host key and enrolls a dedicated restricted SSH key; private keys and agent logins are never included.
3. In **Projects**, select the root folder of your Git project, choose the local agent, select the paired Mac, and save. Add separate projects if both Codex and Claude will work on the same Mac. Both copies need the same committed baseline. Refresh in Team chat after saving.
4. In **Team chat**, select a leader and a worker, describe the task, and start. The leader plans, the worker implements, and the leader reviews the actual implementation diff. Swap roles when creating the next task. You can stop an active team or retry a blocked connection.
5. Read the result, then send a follow-up in the same chat. Follow-ups revise the existing implementation checkout, preserving previous changes. Publishing remains a separate admin decision.

## Architecture

- `aabridge_core.teams`: persistent room assignments, admin messages, stage checkpoints, deterministic task keys, stop/retry, and follow-up revisions.
- `TeamRunner` runs alongside the existing supervisor and uses the same local/SSH protocol in either direction. The admin Mac holds the room; task history and workspaces stay on the Mac running each stage. Keep the admin supervisor running during orchestration.
- A leader stage is read-only. Its plan becomes the follower’s instruction. The follower’s report and independently transferred Git diff become evidence for the leader’s review. The follower cannot reassign the room, create another delegation, or override its leader through task output. Remote identities cannot invoke room-admin operations.
- `NativeWorker` starts the installed Codex or Claude coding CLI in a separate cloned checkout on `codex/task-…`. The CLI can read the whole project, add/edit/delete files and run project commands. Codex uses its workspace-write sandbox; Claude uses normal acceptEdits permissions with explicitly allowed coding tools. No bypass-permissions flags are used. The worker runs under the local signed-in user: this mode does not provide the old proposal sandbox’s OS-level isolation. Use it for projects and paired Macs you trust.
- Native coding removes the old 16-file/16-KiB restrictions and arithmetic-only verification requirement. It uses a one-hour task budget with up to 100 turns/revisions. Application test results are agent-reported, not independently guaranteed by AABridge. Every coding result requires review.
- Existing proposal projects, pinned agent binaries, restricted transport, task ownership, credential privacy and history remain supported. Saving a coding project revalidates the installed binary; an updated binary must be re-enabled locally.
- Task clones have no push remote. Result branches are captured for review, never automatically pushed, merged or deployed. Native CLI instructions prohibit those actions; native coding is not a security boundary against a malicious local process.
- Admin mutations and stage submissions use stable keys. Lost acknowledgements replay the same request. Stage handoffs resume after a supervisor restart; already-interrupted model processes remain explicit failures instead of silently starting replacement sessions. A stop retries cancellation until the current member is reachable.

## Git cooperation with Claude

Read `AGENTS.md` (also referenced by `CLAUDE.md`). Work in separate worktrees and branches. Never share a live checkout, reset another agent’s work, force push, or push directly to main.

Run `python scripts/git_sync.py` before a push. It fetches using the authenticated `gh` HTTPS credential helper, checks a clean feature branch and requires both the latest main and the remote feature branch to be ancestors of HEAD. `--push` uses an ordinary non-force push; a racing branch update is rejected by Git. This avoids overwrites but cannot promise that two independent feature branches will never need integration work. Use reviewed PRs.

## Current limits

This is a development preview, not a production-ready paid product. It runs a sequential leader-plan → follower-code → leader-review cycle; agents do not chat freely or create nested delegations. A review reports defects to the admin rather than automatically launching unlimited repair rounds. A stop is the way to interrupt active work; the next admin message is sent after the task stops or finishes.

Two-way pairing currently uses a public JSON card exchange on a reachable network. It is not QR or zero-configuration discovery. Replacing a saved device’s address or host key requires explicit local configuration review. A project must already be a Git repository with a commit; uncommitted source-folder changes are not included in task clones. The UI explains this before saving.

Room history is retained in SQLite; the desktop shows a bounded recent window, and full task-stage conversations remain in Task history. Automatic review supports diffs up to 64,000 bytes; larger changes block for manual review. A room’s leader review runs against its baseline plus the actual follower diff. Submodules are not automatically initialized. Closing the window does not stop the daemon or the tasks.

The deterministic fixtures verify role reversal, exact handoff direction, real diff review, lost acknowledgements, restart replay, admin-only assignments, stop after a dropped acknowledgement, two-way pinned pairing, follow-up branch continuity, both native CLI output formats and binary pinning. These fixtures do not establish that a physical MacBook/Mac mini pair has completed a live Codex/Claude model run. Run that acceptance test before relying on this preview for customer work.

## Build verification for this preview

171 deterministic tests passed, including the two-supervisor IPC and follow-up test. Offline wheel installation and all 15 MCP tool schemas passed. The universal arm64/x86_64 desktop compiled and passed code-signature and resource smoke checks; the new-task screen was rendered and visually inspected with the 1.2× typography scale. The existing real-Claude sandbox verifier could not run because a Claude CLI is not discoverable on this Mac. No live model turn or physical two-Mac acceptance test was performed.

## Connection-card fix · 0.3.0.dev2

The first team preview incorrectly treated a missing `/etc/ssh/ssh_host_ed25519_key.pub` file as proof that Remote Login was off. macOS can have its SSH service loaded before its host keys exist. Card creation now makes a bounded, unauthenticated check of the local SSH listener when the public file is missing, reads only its Ed25519 public key, and reports a host-key availability problem rather than claiming the setting is off.

Cards now use macOS’s `LocalHostName` plus `.local` for the network address and keep `ComputerName` only for display. This fixes Macs whose display hostname contains spaces or an apostrophe. Pasting an SSH command into the card field now gives instructions for copying an actual connection card.

Verification: card creation succeeded against the affected MacBook’s real AABridge state and SSH identity using `Sparsh-MBP.local`. All 177 deterministic tests passed, including missing-file/live-listener fallback, timeout and malformed-key handling, and display/network name separation. No model calls or remote agent tasks were started.
