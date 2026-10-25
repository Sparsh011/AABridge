# Full product requirements

Original supplied brief. The staged implementation scope is recorded in
[milestones.md](milestones.md); this document does not claim these features exist.

Subsequent user clarification (2026-10-24): agent choice must be independent of
host location; support adapters for Gemini and other compatible coding agents,
and use Codex-to-Codex for the first real communication test while Claude access
is unavailable. See [agent compatibility](agent-compatibility.md). The original
brief below is preserved verbatim as historical input.

Build a standalone, reusable communication bridge between Codex and Claude Code.

This is a separate project, unrelated to Sparshur. Build the working tool, tests, installation instructions, and security documentation—not just a design or prototype.

Both SAME-COMPUTER and TWO-COMPUTER operation are mandatory deliverables. Do not implement only local mode and describe remote mode as future work.

PRODUCT GOAL

I want to give an instruction to Codex, have it delegate a bounded development task to Claude Code, receive questions/results, request revisions, and review the returned changes without manually forwarding messages.

The reverse direction must also work: Claude Code can delegate to a managed Codex worker and receive its questions/results.

Support:
1. Codex and Claude Code on the same MacBook.
2. Codex on a MacBook communicating with Claude Code on a Mac mini.
3. Configurable agent roles and host locations, without hardcoding my usernames, devices, directories, or repositories.

Other people should be able to install this tool and connect their own machines and agents. No central public service or account system is required.

FIRST ACTIONS

- Inspect this workspace and applicable instructions.
- Check installed runtimes and coding-agent CLI versions.
- Read current official Codex and Claude Code documentation for MCP, programmatic execution, session resumption, authentication, permissions, and sandboxing.
- Verify capabilities against the actual installed versions; do not invent flags or rely on obsolete integration examples.
- Write a concise implementation plan and threat model, then proceed with implementation.
- Ask early for genuinely missing remote-host details/access, while continuing independent local work.
- Never ask me to paste passwords, private keys, API keys, or subscription tokens into chat.

ARCHITECTURE

Choose a small, maintainable architecture and explain the stack choice briefly. Avoid unnecessary infrastructure, dashboards, containers, or dependencies.

Provide:
- A local broker/supervisor with durable task and event storage.
- An MCP interface usable by both Codex and Claude Code.
- Agent adapters that start/resume dedicated, programmatically controlled Codex and Claude Code sessions.
- Local communication over an appropriately protected IPC mechanism.
- Remote communication through an authenticated, encrypted transport. Prefer standard SSH with verified host keys and a restricted RPC entry point unless there is a concrete reason to choose otherwise.
- A CLI for setup, pairing/enrollment, health checks, task status, cancellation, and troubleshooting.

Keep transport separate from task/session logic so local and remote modes share the same protocol.

Use established security libraries and protocols. Do not invent cryptography.

AGENT AND SESSION BEHAVIOR

- Support task submission, status retrieval, event waiting, questions, answers, results, revision requests, and cancellation.
- Return task IDs promptly; submitting work must not require keeping one connection open until completion.
- Preserve exact agent session IDs and resume the correct session.
- Never use “resume the most recent conversation” when that could select the wrong task.
- Serialize access to each session and worktree.
- Do not scrape terminals, simulate typing, or hijack a manually opened agent session.
- An installed/authenticated agent does not imply that an unattended worker can use it: verify operation under the actual runtime account.

Distinguish:
1. Active interactive agents using MCP tools.
2. Managed worker sessions started/resumed by the supervisor.

Do not promise that MCP alone can wake an arbitrary idle desktop chat or inject messages into it. Document the supported experience accurately.

At least one active coordinator must be able to complete a delegated task, answer a worker question, receive results, and request a revision without human message forwarding.

When awaiting clarification, persist the question and checkpoint without requiring continuous model inference. Resume the worker when an authorized answer arrives.

TASK CONTRACT

Use versioned, validated structured messages, including:
- Task and attempt IDs.
- Authenticated sender and recipient identities.
- Project ID resolved through local configuration.
- Exact base commit.
- Objective, scope, acceptance criteria, and relevant context references.
- Explicit permissions/resource limits.
- Idempotency key.
- Task revision.
- Question IDs and event cursors.
- Structured result and verification evidence.

Do not accept arbitrary filesystem paths, executable paths, or shell commands from peers as authority.

A task ID is not a credential.

Git transfers source commits; the bridge transfers tasks, questions, and results. Support configured Git remotes without requiring a GitHub account integration for basic operation.

Use isolated working copies/worktrees. Never modify the user’s active checkout or discard existing changes automatically.

SECURITY REQUIREMENTS

These are core implementation requirements, not optional hardening.

Write down the threat model, assumptions, supported protections, and residual risks. Do not claim that compromise is impossible.

Protect against:
- Unenrolled peers and sender impersonation.
- Man-in-the-middle attacks during remote communication.
- Unauthorized project/task access.
- Replayed or duplicated mutations.
- Cross-task message injection and stale answers.
- Path traversal, symlink escape, and shell interpolation.
- Prompt injection and attempts to expand permissions.
- Secret leakage in messages, logs, or artifacts.

Remote enrollment:
- Require explicit local approval of peers.
- Verify remote identity through a trusted setup channel.
- Pin SSH host keys if SSH is selected; do not silently trust or replace unexpected keys.
- Support revocation.
- Restrict peers to their assigned projects, roles, and operations.
- Do not expose an unauthenticated public listener.

Local mode:
- Protect IPC endpoints and credential/state files.
- Validate ownership and permissions.
- Explain what protection exists against other OS users and what cannot be guaranteed against malicious processes running as the same user.
- Do not describe localhost or same-user access as inherently trustworthy.

AUTHORIZATION AND DANGEROUS OPERATIONS

The bridge must not expose a generic “execute this received shell command” tool.

Exchange bounded development tasks and structured operations. Receiving text from another agent must never grant execution privileges.

Default policy:
- Deny privilege escalation, sudo, and permission/sandbox bypass.
- Deny destructive deletion, bulk cleanup, repository removal, destructive Git reset/clean, force-push, and similar operations.
- Deny access to unrelated repositories, personal files, credentials, and production data.
- Deny peer-initiated changes to bridge policy, installed binaries, credentials, or peer enrollment.
- Deny automatic merges and deployments.
- Do not enable blanket approval bypasses to make unattended execution appear successful.
- A worker cannot approve its own permission request or impersonate the human/coordinator.
- A coordinator cannot grant permissions beyond the locally configured human-authorized scope.

A peer message claiming urgency, emergency, authority, or that “someone will die unless you run this command” must not bypass policy. Reject the prohibited action and record/surface the request appropriately.

Do not rely on keyword matching for “sudo”, “rm”, or similar strings. Dangerous actions can occur through interpreters, file tools, build scripts, and indirect execution.

Enforce restrictions through appropriate OS/process/filesystem/network controls and supported agent permission mechanisms. Keep policy enforcement outside model-generated instructions.

Distinguish:
- Rejecting commands received from peers.
- The worker independently generating actions while implementing a legitimate task.

Both execution paths require appropriate restrictions.

A narrow set of locally configured verification profiles may run within isolation. Treat repository build/test scripts as executable code, not automatically safe input.

Do not claim a read/write worktree or a diff check prevents deletion, exfiltration, or arbitrary shell side effects.

If a requested hard guarantee cannot be enforced in the selected environment, state that clearly and fail closed for the affected capability. Do not silently substitute prompt rules for enforcement.

Do not provide automatic deletion/cleanup in the initial version. Any later destructive operation requires a separate explicit local human authorization mechanism.

AUTHENTICATION AND SUBSCRIPTIONS

Prefer supported existing local Codex/Claude account authentication where available.

Do not assume a subscription includes API usage. Do not silently switch to API billing, enable paid overages, or select costly models without authorization.

Keep provider credentials on the machine where needed. Never commit or transmit them through task messages.

Allow resource limits for runtime, turns, revision rounds, concurrent workers, and spending where supported. Clearly distinguish estimated usage from guaranteed billing limits.

RELIABILITY

- Persist tasks/events before acknowledging mutations.
- Deduplicate retried requests; reject reuse of an idempotency key with different content.
- Support ordered event retrieval with resumable cursors.
- Keep remote work independent of an SSH/MCP connection’s lifetime.
- Handle reconnects, authentication failures, model limits, timeouts, and permission denials explicitly.
- Never treat exit code zero alone as proof of successful task completion.
- Recover conservatively after supervisor restarts; reconcile surviving workers before starting replacements.
- Cancellation must stop the owned worker/process tree with bounded escalation and preserve partial work.
- Closing a connection must not mean cancellation.
- Do not claim exactly-once execution of arbitrary shell side effects.

Return concise summaries to agents and retain bounded detailed logs locally. Avoid repeatedly loading entire repositories or forwarding huge logs into model context.

RESULTS AND SOURCE CONTROL

A result must include:
- Base and result commit SHAs.
- Branch/worktree reference.
- Changed paths.
- What was implemented.
- Actual verification commands/profile names and exit outcomes.
- Outstanding issues and testing limitations.

Independently inspect changes and evidence before presenting success.

Creating commits in assigned disposable/task worktrees is allowed. Publishing requires an explicitly configured and authorized destination. Never push to a default branch or merge automatically.

TESTING AND COMPLETION CRITERIA

Build automated tests using fake agent adapters first, followed by bounded tests with real installed agents.

Required coverage:
- Local task submission and result delivery.
- Remote task submission and result delivery.
- Both Codex→Claude and Claude→Codex adapters.
- Question/answer exchange and exact-session resumption.
- Revision loop.
- Duplicate requests and replay rejection.
- Unauthorized peers/projects/tasks.
- Host identity mismatch.
- Malformed messages and path/symlink attacks.
- Attempts to send terminal commands or override policy through coercion.
- Destructive actions and privilege escalation denial.
- Disconnect/reconnect, worker failure, restart recovery, and cancellation.

Demonstrate real same-computer communication on the MacBook.

Demonstrate real two-computer communication between the MacBook and Mac mini once access is available. At minimum, complete one remote task with a question/answer round trip and a reviewable result.

A localhost simulation, fake SSH test, or two processes on one machine does not count as physical two-device verification.

If the mini is unavailable:
- Complete everything that can be built/tested locally.
- Provide exact remaining setup and verification steps.
- Clearly report remote verification as pending.
- Do not mark the whole project complete.

Use a disposable sample repository with deterministic tests for demonstrations. Do not use Sparshur or modify any real application’s crypto/protocol.

DELIVERABLES

- Working standalone source code.
- Versioned task/event schemas.
- Automated tests.
- Reproducible build/install instructions.
- Same-device quick start.
- Two-device quick start.
- Configuration examples without secrets.
- Codex and Claude MCP configuration instructions.
- Enrollment/revocation and security-policy documentation.
- Threat model and explicit limitations.
- Troubleshooting, recovery, and uninstall instructions.
- Evidence distinguishing fake-adapter tests, real local-agent tests, and real remote-agent tests.

Keep the MVP small, but BOTH LOCAL AND REMOTE SUPPORT ARE REQUIRED. Avoid speculative features such as agent marketplaces, public discovery, recursive delegation, or a web dashboard.

Proceed autonomously with reversible implementation work. Ask only for genuinely missing information, remote access/setup, or actions requiring my authorization. Report what works, what was tested, and what remains pending honestly.
