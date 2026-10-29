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

## Autonomy, admin notes and result delivery

A new task picks three things in the desktop form (`team_start` fields; omitting them keeps the old single plan → build → review pass):

- **`approval`**. `ask`: the room pauses in `awaiting_admin` after the plan, and after any review that requests changes. The admin answers with `team_decide`: `continue`, `replan` (needs a note) or `finish`. `auto`: the leader owns the task. A review that ends `VERDICT: CHANGES_REQUESTED` sends its points back to the follower, which is told to investigate each one and either fix it or explain with evidence why it disagrees. Each role resumes its own task and session, so the leader re-reviews with memory of its earlier review, against the full cumulative diff. The loop stops at `VERDICT: APPROVED`. After `max_fixes` rounds (default 3), or a review with no verdict line, the room asks the admin instead.
- **`placement`**. `sandbox` (the default) keeps the result in the isolated task clone. `branch` copies the follower's commit into the admin's project as `<agent>/<task-slug>-<room suffix>`. It never adopts an existing branch name; a collision gets a `-2` suffix. `worktree` does the same and adds a Git worktree next to the project in `<project>-worktrees/`. `current` fast-forwards the checked-out feature branch, and refuses main/master/the default branch, a dirty tree or a branch that moved. `pull_request: true` pushes the branch without force, using the admin's `gh` login, and opens or reuses one pull request. The leader's reviews are posted on it as comments. Delivery runs on the admin Mac, so one member must be `local`, and it repeats after every fix round. A delivery failure is reported in the chat and never stops the team.
- **Merging** happens only on `team_merge`, which is the desktop Merge button (`gh pr merge --merge`). Nothing is merged automatically.

When a stage fails, the runner records `failure` with the problem, the fix, the Mac and the project, and posts it as an AABridge message, not as the agent speaking. Fixable setup failures (agent updated, agent missing, restart, cancellation) pause the room as `blocked`. **Try again** then starts a fresh task for that stage, instead of replaying the failed one under the same key. Connection and baseline errors also name the Mac and the commits involved.

`team_guide` sends an admin note while the team works. Notes are queued durably and attached to the next agent turn. If one arrives mid-step, the runner adds a turn: the leader re-plans, or the worker adjusts before review. The note never interrupts a running CLI process. Task clones now use the working agent's prefix (`claude/task-…` or `codex/task-…`); older `codex/` clones are still accepted.

## Git cooperation with Claude

Read `AGENTS.md` (also referenced by `CLAUDE.md`). Work in separate worktrees and branches. Never share a live checkout, reset another agent’s work, force push, or push directly to main.

Run `python scripts/git_sync.py` before a push. It fetches using the authenticated `gh` HTTPS credential helper, checks a clean feature branch and requires both the latest main and the remote feature branch to be ancestors of HEAD. `--push` uses an ordinary non-force push; a racing branch update is rejected by Git. This avoids overwrites but cannot promise that two independent feature branches will never need integration work. Use reviewed PRs.

## Current limits

This is a development preview, not a production-ready paid product. It runs a sequential leader-plan → follower-code → leader-review cycle; agents do not chat freely or create nested delegations. Automatic repair rounds are bounded by `max_fixes` (1–10), and the verdict is read from the leader's final `VERDICT:` line, so it relies on the leader following that instruction. Admin notes reach the agents at step boundaries, not mid-turn. A stop is the way to interrupt active work. Pull requests need a GitHub `origin` and a signed-in `gh` on the admin Mac. Each native task still has its one-hour runtime budget across all of its fix revisions.

Two-way pairing currently uses a public JSON card exchange on a reachable network. It is not QR or zero-configuration discovery. Replacing a saved device’s address or host key requires explicit local configuration review. A project must already be a Git repository with a commit; uncommitted source-folder changes are not included in task clones. The UI explains this before saving.

Room history is retained in SQLite; the desktop shows a bounded recent window, and full task-stage conversations remain in Task history. Automatic review supports diffs up to 64,000 bytes; larger changes block for manual review. A room’s leader review runs against its baseline plus the actual follower diff. Submodules are not automatically initialized. Closing the window does not stop the daemon or the tasks.

The deterministic fixtures verify role reversal, exact handoff direction, real diff review, lost acknowledgements, restart replay, admin-only assignments, stop after a dropped acknowledgement, two-way pinned pairing, follow-up branch continuity, both native CLI output formats and binary pinning. These fixtures do not establish that a physical MacBook/Mac mini pair has completed a live Codex/Claude model run. Run that acceptance test before relying on this preview for customer work.

## Build verification for this preview

171 deterministic tests passed, including the two-supervisor IPC and follow-up test. Offline wheel installation and all 15 MCP tool schemas passed. The universal arm64/x86_64 desktop compiled and passed code-signature and resource smoke checks; the new-task screen was rendered and visually inspected with the 1.2× typography scale. The existing real-Claude sandbox verifier could not run because a Claude CLI is not discoverable on this Mac. No live model turn or physical two-Mac acceptance test was performed.

## Connection-card fix · 0.3.0.dev2

The first team preview incorrectly treated a missing `/etc/ssh/ssh_host_ed25519_key.pub` file as proof that Remote Login was off. macOS can have its SSH service loaded before its host keys exist. Card creation now makes a bounded, unauthenticated check of the local SSH listener when the public file is missing, reads only its Ed25519 public key, and reports a host-key availability problem rather than claiming the setting is off.

Cards now use macOS’s `LocalHostName` plus `.local` for the network address and keep `ComputerName` only for display. This fixes Macs whose display hostname contains spaces or an apostrophe. Pasting an SSH command into the card field now gives instructions for copying an actual connection card.

Verification: card creation succeeded against the affected MacBook’s real AABridge state and SSH identity using `Sparsh-MBP.local`. All 177 deterministic tests passed, including missing-file/live-listener fallback, timeout and malformed-key handling, and display/network name separation. No model calls or remote agent tasks were started.


## Live activity and task model settings · 0.3.0.dev3

The new build adds a separate Live activity panel to Team chat. The supervisor records public progress messages, tool starts/completions, file-change paths, and reported model information. The coordinator relays authorized task pages into the room, labeling each update with the leader/follower, device, and planning/building/review stage. The viewer polls each second, merges partial-message snapshots by stable ID, shows source-update age separately from service-check age, and can pause automatic scrolling with Follow live. CLI/provider quiet periods are shown honestly; there is no fabricated percentage or invented reasoning.

Private reasoning/thinking blocks and thinking deltas are never displayed or stored by the activity parser. Commands, tool inputs, and raw stdout/stderr are omitted; recognizable credential patterns in public text are redacted. The feed is a progress view rather than a complete terminal transcript. It keeps bounded excerpts and a recent viewer window while preserving the durable activity history. Default retention is local alongside the task database.

Each leader and follower can choose a model and effort independently when starting a team. Completed/stopped rooms offer Models for the next message, which applies only to the next round. The single-task composer also offers settings for full coding projects. Agent default omits an override and uses that Mac’s saved CLI settings; Custom model ID allows an exact supported ID. Presets are convenient suggestions, not a live account-entitlement catalog. Account, model and CLI version determine which choices actually work. Claude aliases such as opus may resolve to a newer version; a version reported by the CLI is shown as an activity event. Codex’s selected setting is recorded without claiming the stream reported a resolved default when it did not.

Native invocations pass explicit `--model` and per-agent effort arguments on both fresh and resumed sessions. Model/effort data is validated as protocol fields and passed as argv, never interpolated into shell code. A task revision can change settings without discarding the existing session or implementation checkout. Full coding is required; legacy proposal projects remain on their original execution path. Receivers advertise capabilities, and a participant missing model-selection support is rejected before starting any model work rather than silently ignoring a selected option.

`activity` and `team_activity` are read-only cursor APIs, available as `bridge_activity` and `bridge_team_activity` in MCP. Pages are bounded, task ownership/project grants apply, relay replay is deduplicated, and activity transport failures do not fail, cancel or resubmit the implementation task. A read-only feed poll does not start a model turn. Existing saved rooms can be read without changing their assignments.

Install this preview only after active work finishes. Update the bridge service on the admin Mac and the participating Macs through Settings → Set up this Mac, then refresh Team chat. The new frontend cannot recover intermediate events discarded by an older running worker. No installed app, user configuration, running service or active task was replaced/restarted while building this release. Validation uses disposable services, fake agent executables, and offscreen UI previews; it is not a claim of a live physical two-Mac model run.

Protocol references: [Codex JSONL events](https://learn.chatgpt.com/docs/non-interactive-mode), [Codex CLI controls](https://learn.chatgpt.com/docs/developer-commands?surface=cli), [Claude CLI flags](https://code.claude.com/docs/en/cli-reference).

Release checks: 194 deterministic tests passed, including public streaming before completion over two disposable supervisors, independent model choices and revision changes, privacy filtering, cursor replay, ownership, and old-service compatibility. The wheel passed offline installation and all 17 MCP tools loaded their schemas. The universal desktop passed signing/resource checks, and both model-selection and live-feed views were rendered for visual inspection. The installed native worker was separately verified to still match the previous 0.3.0.dev2 wheel.
