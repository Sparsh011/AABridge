# Team collaboration preview · 0.3.0.dev1

AABridge now treats every Mac as a peer. A room has a human admin, a leader chosen by that admin, and a follower chosen by that admin. Either Codex or Claude on either Mac can lead. The assignment belongs to the room and does not change global device authority.

## Use it

1. Install the same AABridge Team build on both Macs. In Settings choose the installed agent and select **Set up this Mac**. Full coding setup does not require a demo or a special Claude token. Sign in to the agent CLI on its own Mac.
2. In **Devices**, create and copy each Mac’s public connection card to the other Mac, then connect it. Exchange both cards. New cards pin each app’s TLS certificate and enable its direct listener; Remote Login is not required. Private keys and agent logins are never included. Keep both Macs reachable over a LAN or VPN.
3. In **Projects**, select the root folder of your Git project, choose the local agent, select the paired Mac, and save. Add separate projects if both Codex and Claude will work on the same Mac. Both copies need the same committed baseline. Refresh in Team chat after saving.
4. In **Team chat**, select a leader and a worker, describe the task, and start. The leader plans, the worker implements, and the leader reviews the actual implementation diff. Swap roles when creating the next task. You can stop an active team or retry a blocked connection.
5. Read the result, then send a follow-up in the same chat. Follow-ups revise the existing implementation checkout, preserving previous changes. Publishing remains a separate admin decision.

## Architecture

- `aabridge_core.teams`: persistent room assignments, admin messages, stage checkpoints, deterministic task keys, stop/retry, and follow-up revisions.
- `TeamRunner` runs alongside the existing supervisor and uses the same local/SSH protocol in either direction. The admin Mac holds the room; task history and workspaces stay on the Mac running each stage. Keep the admin supervisor running during orchestration.
- Planning is read-only. The plan becomes the follower’s instruction. Review receives the exact implementation commit in an isolated checkout. Review can run checks using private scratch/cache directories, but must leave the checkout and HEAD unchanged; the bridge rejects source edits. The follower’s report and transferred Git evidence inform the review. The follower cannot reassign the room, create another delegation, or override its leader through task output. Remote identities cannot invoke room-admin operations.
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

## Linked projects (multi-repository work)

A coding project can list up to 8 **related projects** on the same Mac (Projects → step 4), each with **Can read** or **Can edit** access. One example is a Go server linked to its Android and Mac apps. The Mac that owns the project sets these links, and peers only see the link names and access levels, never local paths. A peer granted the main project gets its links; nothing else on that Mac is exposed.

- **Where the copies live.** When a task starts, `aabridge_workspace.linked` clones each linked repository at its current commit into `workspaces/<task>.linked/<name>`, next to the main task checkout. Each clone is on the same task branch and has no `origin`. The admin's folders are never written to during the task.
- **What the agents are given.** The prompt lists every linked folder with its access level. Codex gets only the **Can edit** folders as writable roots, through `sandbox_workspace_write.writable_roots`, so its sandbox keeps read-only links read-only. Claude Code gets every linked folder via `--add-dir`.
- **After each turn.** AABridge commits changes in edit links. Anything changed in a read link, or in any link during plan and review, is reset and noted in the result. Each changed link's diff goes into the leader's review alongside the main diff, within the same 64,000-byte limit.
- **Delivery.** Edit-link changes use the same placement as the main project and the same branch name, each with its own pull request when PR delivery is on; reviews are posted on every PR. **Merge** merges them all, and skips ones already merged when retried. Linked changes are only copied into the admin's folders when the worker runs on the admin Mac. Otherwise they stay in that Mac's task workspace and the chat says so.
- **Limits.** Link baselines are taken at task start, not pinned when the project is saved, so the two Macs only need to match on the main project's commit. Not tested yet with live agents on real Android, Mac and Go repositories.

## Git cooperation with Claude

Read `AGENTS.md` (also referenced by `CLAUDE.md`). Work in separate worktrees and branches. Never share a live checkout, reset another agent’s work, force push, or push directly to main.

Run `python scripts/git_sync.py` before a push. It fetches using the authenticated `gh` HTTPS credential helper, checks a clean feature branch and requires both the latest main and the remote feature branch to be ancestors of HEAD. `--push` uses an ordinary non-force push; a racing branch update is rejected by Git. This avoids overwrites but cannot promise that two independent feature branches will never need integration work. Use reviewed PRs.

## Shell startup and speed

The desktop app runs `Activity.command` with `zsh -f`, so the user's zsh startup files no longer run for each UI call. One Mac mini had a `~/.zshenv` line that appended a copy of itself every time a shell started. After about 8,000 copies, each call took 7.7 s instead of 0.11 s.

SSH requests from the paired Mac still run through the receiving user's login shell, because sshd runs the forced command with `$SHELL -c` and zsh always reads `~/.zshenv`. **Check prerequisites** now inspects that file without running it, and warns about self-appending or heavily duplicated lines.

## Build integration · 0.3.0.dev5

PR #10 incorporates the linked-project and connection-error updates from main.
Project discovery advertises both shared-history capabilities and linked-project
names/access while continuing to omit receiver-local paths. Shared-history
ownership and read-only controls remain in effect.

`make build` now replaces the single Git-ignored `build/AABridge.app` (override
with `APP_PATH`). Setup files and the Python wheel are bundled directly into the
app, with temporary packaging files cleaned up and no generated ZIPs. The native
builder verifies both architectures, signature and resources before publishing;
it preserves the prior app on build failure and rolls back a failed replacement.
This does not update a running installed receiver: use Set up this Mac after
building, on both Macs when updating their shared-history protocol support.

The check/build subprocess environment derives source roots from the current
`pyproject.toml` package map. This fixes `aabridge_transport_direct` import errors
after pulling into an older editable Python installation, without relying on
that installation's generated module finder or modifying the user's environment.

Integration validation: `make build` passed all 243 deterministic tests and
created the app directly with both arm64/x86_64 images and valid signature/
resources. The embedded wheel passed offline installation and all 20 MCP/schema
checks. The native shared-history render confirmed answer/cancel send nothing
for a receiver-local task. Regression checks cover source imports with no
editable finder, repeated app replacement, rollback after publication failure,
and preserving unrelated/symlinked output. Linked-project discovery was also
rechecked with both linked names/access and shared-history capabilities present.
No paid model calls or physical two-Mac model execution were performed.

## Shared Task history · 0.3.0.dev5

Task history previously returned only tasks submitted by the connecting peer.
A task started locally on the mini has sender `local`, so it was absent from a
MacBook's remote list even when that MacBook had project access. This is separate
from Team chat, whose room controls and local-admin boundary are unchanged.

Task history now requests `shared: true` for remote list/status/conversation/
event reads. The receiver may additionally return locally created `coding-v1`
tasks in a currently registered coding project explicitly granted to this peer.
Those tasks are read-only: listing/status carries `permissions.can_control:
false`, the desktop shows that state, and cancel/answer/revise remain denied by
the server. Other peers' tasks remain private even with shared reads. Proposal
tasks are not shared by this extension. Current project grants, peer enablement
and project type are rechecked on every request. Revocation takes effect without
changing task owners, assignments, workspaces or saved conversation records.

The same boolean is available on CLI read commands with `--shared` and through
MCP read-tool bodies. Runtime validation and packaged request/task schemas agree;
`shared` is rejected on mutations. Default CLI/MCP reads retain owned-only
behavior. Source-repository or Git result access is not expanded by this flag.

Update the app/installed receiver on both Macs, then select the mini in Task
history. An old receiver falls back to its owned-task list with a visible update
notice; the app does not present that subset as synchronized shared history.
The viewer sorts the collected pages by last update so continued older tasks
are visible near the top. Wire pagination still uses the existing stable row
cursor. A legacy alias and a newer device card can authenticate as different
peers with different project grants; neither route's permissions are silently
copied to the other. Allow the intended paired Mac in the source project's
settings if that connection lacks the project.

This is a live authorized history read, not database replication. The source
Mac must be reachable, and its history stays on that Mac. The full supplied
context for shared coding tasks is readable to explicitly granted project peers;
provider credentials and raw/private reasoning are not added to history.

Validation: 230 deterministic tests passed, including a receiver-local coding
task read from another authenticated TLS peer through CLI/MCP, ownership and
project/revocation checks, strict read-only flags, mixed-owner pagination, and
visible fallback for older receivers. The offline wheel and all 20 MCP/schema
checks passed. The desktop compiled for arm64/x86_64, passed signature/resource
smoke checks, and its isolated shared-history render verified that answer/cancel
created no outbox send. No paid model calls or new physical two-Mac model run
were performed. Existing physical SSH reads confirmed the mini was reachable
and its two connection identities returned different owned-task subsets.

## Current limits

This is a development preview, not a production-ready paid product. It runs a sequential leader-plan → follower-code → leader-review cycle; agents do not chat freely or create nested delegations. Automatic repair rounds are bounded by `max_fixes` (1–10), and the verdict is read from the leader's final `VERDICT:` line, so it relies on the leader following that instruction. Admin notes reach the agents at step boundaries, not mid-turn. A stop is the way to interrupt active work. Pull requests need a GitHub `origin` and a signed-in `gh` on the admin Mac. Each native task still has its one-hour runtime budget across all of its fix revisions.

Two-way pairing currently uses a public JSON card exchange on a reachable network. It is not QR or zero-configuration discovery. Replacing a saved device’s address or pinned identity requires explicit local configuration review. A project must already be a Git repository with a commit; uncommitted source-folder changes are not included in task clones. The UI explains this before saving.

## Direct connections · 0.3.0.dev4

New version-2 connection cards use an app-owned TLS listener (TCP 47631 by
default), with mutual certificate authentication and exact SHA-256 certificate
pins. Creating a card opts this Mac into listening; the supervisor opens the
port itself and keeps it available while running. It does not enable Remote
Login, inspect system SSH host keys, or modify authorized_keys. macOS may still
request local-network/firewall permission. Agent credentials stay on their own
Macs. A host/address override in Devices supports reachable VPN addresses;
there is no relay, NAT traversal, or automatic cross-network discovery.

The transport calls the same request validator and Store under the certificate's
enrolled peer identity, with live project grants, task ownership, revocation,
admin-only operations and idempotency unchanged. Client and server TLS trust
stores contain only explicit paired certificates; an exact certificate check
also prevents a certificate signed by a paired device from assuming its identity.
The app identity is created locally with macOS OpenSSL and private 0600 files.
Certificates last ten years; expiry or identity replacement needs explicit local
review and a new pairing. No third-party Python runtime dependency was added.

RPC uses one bounded TLS exchange per operation, without SSH or a remote login
shell. Result transfer uses a checksummed Git bundle over the same authenticated
channel, then verifies the exact result commit in a private local review repo.
The bundle includes reachable history and is capped at 64 MiB; the existing
64,000-character review-diff limit still applies. Large repositories may exceed
that bundle limit. There is no shared network filesystem or arbitrary path/file
download endpoint.

Upgrade both installed receivers using Set up this Mac, then exchange fresh
cards in both directions. An explicit version-2 card upgrades that device's old
SSH route while preserving project grants and history. It does not silently
replace an existing TLS pin, re-enable revoked peers, or downgrade TLS to SSH.
Existing SSH remotes and version-1 cards remain supported until upgraded; their
Remote Login requirement still applies. Legacy SSH grants are preserved for
local review rather than deleted automatically.

Claude's startup-speed fix (PR #6) is integrated from main. Its shell launch
optimization remains useful for local desktop backend startup; direct RPC
additionally avoids launching SSH and the remote shell per request.
Direct reachability failures use `direct_unavailable` so Claude's separate
`claude/clear-team-errors` UI mapping cannot substitute legacy Remote Login
instructions for this route. Its branch does not need to be merged first.
The existing setup ZIP still carries a legacy public key for compatibility;
direct runtime pairing uses its own TLS identity and does not consume that key.
Physical two-Mac model execution remains unverified; local TLS fixtures do not
establish that acceptance result.

Validation after integrating PR #6: 222 deterministic tests passed, including actual mutual-TLS task
submission, questions/answers, ownership/revocation, CLI/MCP routing, result
bundle fetch, signed-certificate impersonation rejection, and a full native
leader/follower/review/follow-up cycle with fixture agents. The offline wheel
installed and loaded all 20 MCP tools and packaged schemas. The desktop compiled
for arm64 and x86_64 and passed ad-hoc signature and resource smoke checks.

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

## Build runtime selection

Builds discover local runtimes using existing configuration and fixed search
locations. Missing runtimes prompt only on a terminal and can be skipped.
Validated selections persist under ignored build/runtime-paths.json and seed
receiver setup. Explicit CODEX_PATH/CLAUDE_PATH overrides fail early if invalid.
This does not execute an agent, install a runtime, or change project approvals.
App preferences with empty/missing locations fall back to bundled selections;
existing usable user choices remain authoritative. Cross-Mac paths still need
selection on the receiving Mac, especially when external drives differ.


## GitHub delivery and exact review handoff · October 31, 2026

Two saved service-room histories and the discussion on Sparshur-Service PR #1
showed the same failure: the leader stayed at baseline `050c454` while the
worker's newer commits lived in a different task clone. The coordinator fetched
the real result but sent only diff text to the leader. Missing-commit and
permission complaints then consumed review/fix rounds even when code findings
had been addressed. The latest room also failed every GitHub push: its SSH
origin bypassed the HTTPS credential helper despite a working `gh` login.

- Team review submissions and revisions now carry `review_snapshot`, a bounded
  base64 Git bundle plus the exact implementation commit. The receiving workspace
  verifies the bundle, exact commit identity, and ancestry from the locally
  approved baseline before checking out the implementation on its own task
  branch. Successive reviews keep the leader session while refreshing its files.
  A review result must retain that exact head and a clean checkout. Planning and
  work cannot accept a review snapshot. Project grants, ownership and revision
  checks still apply. MCP uses the same request schemas as desktop/CLI delivery.
- The leader's updated receiver must advertise `review_snapshot`. Older receivers
  stop before a review model turn with an update instruction. Both participating
  bridge services should be updated after active work ends. Previously discarded
  intermediate agent messages cannot be recovered by this change.
- Review with a verified snapshot can run shell checks. Codex uses workspace-write
  for its isolated review clone plus a private task scratch root; Claude gets
  Bash without Edit/Write tools. Temporary files, XDG caches and Go build caches
  use that private root. Agents are instructed to keep sources/HEAD unchanged,
  and post-run Git validation enforces the accepted result. This is native coding
  with the signed-in user's permissions, not a security boundary for hostile
  repository tools. Missing dependencies or platform tools remain real limits.
- Bundles contain only history after the baseline and are capped at 60,000 bytes
  before base64. The full RPC is checked against the existing 128 KiB limit before
  submission. Oversized evidence stops for manual review instead of falling back
  to reviewing old files. Linked-project changes remain explicit diff evidence;
  their exact working trees are not transferred to the reviewer in this version.
- GitHub pushes rewrite GitHub SSH origins to HTTPS for that command only and use
  `gh auth git-credential`; saved remotes are unchanged. Failed push/PR creation
  keeps the placed branch, commit and linked delivery state, so fix rounds retry
  the same branch rather than allocating `-2`, `-3`, etc. Normal pushes still
  reject concurrent remote updates; there is no force push or automatic merge.
  Admin-requested merges use `--match-head-commit` for the delivered revision
  and reject failed delivery, so a changed PR head cannot silently be merged.
- Agent prompts explain that the bridge owns commit capture, branch delivery
  and PR publication. Workers may integrate a task-required target branch into
  their isolated feature branch; the no-merge restriction applies to PRs and
  protected/source-project branches, not that local conflict-resolution step. Review comments identify the implementation commit. Posting errors
  are saved as `publication.review_errors` and shown in team chat. These are PR
  conversation comments, not inline review threads that GitHub can mark resolved.

These fixes do not implement simultaneous leader/follower editing or reciprocal
cross-repository reviews. The configured workflow is still plan → implement →
review. Merely cloning other repositories does not register linked projects.
Existing rooms retain their assignments and baselines. Real target-branch
conflicts, incompatible client protocols and missing device acceptance results
still require implementation work; this change does not approve or merge those
PRs or modify the installed app/running services.

Validation: `.venv/bin/python scripts/check.py` passed all 258 deterministic
checks. The local IPC and mutual-TLS fixture teams verified that the leader's
HEAD and files match the worker's exact revision, including follow-up turns.
An additional replay used the captured failing room's real `a9d1b10` commit:
a baseline-only `050c454` reviewer imported its 21,145-byte bundle, resolved
the exact SHA and inspected the new `accept.go` file with a clean checkout.
No paid model calls, physical two-Mac model run, installed-app replacement or
service restart was performed. This is a source PR, not a shipped desktop build.
