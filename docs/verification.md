# Verification evidence

Evidence recorded on 2026-10-24 through 2026-10-26, Asia/Kolkata, on this Apple Silicon Mac.
The full project is not complete; distinguish each category below.

| Category | Actual status |
| --- | --- |
| Deterministic unit/service/installer tests | 140 pass; no model calls |
| Real local protected IPC/proxy/MCP subprocess | Passed within deterministic suite |
| Real Codex worker, coordinator through CLI | Question → answer → exact-session resume → checked Git commit → revision passed |
| Real OpenSSH on localhost | RPC, replay, Git transfer, host mismatch, generic command denial and revocation passed with a non-model result fixture |
| Physical MacBook–Mac mini | Pinned SSH and first real Codex question passed; answer delivery passed; result/resumption blocked on receiver cleanup fix deployment |
| Live coordinator through installed MCP configuration | Not demonstrated; MCP subprocess/IPC evidence is separate |
| Claude Code managed worker | `tests/unit/test_claude.py` and `scripts/verify_claude.py` (real CLI 2.1.308 in the sandbox, local fake provider, question + exact resume) pass; live subscription task pending |
| Gemini managed worker | Not implemented or tested |
| General arbitrary-code safety / complete IPC isolation | Not claimed; only documented proposal-v1 controls tested |

## Automated and packaging checks

- `.venv/bin/python scripts/check.py`: 140 tests passed on 26 October, exit 0,
  approximately 10 seconds. Includes durability across reopen, exact retries/conflicts,
  authentication/revocation, cross-peer/task access, strict malformed/duplicate
  JSON/path handling, stale answers/revisions, event cursors, conservative
  recovery, active cancellation, owned-process TERM/KILL, permission-request
  denial, failure redaction, session binding, source isolation, hardlink/symlink
  rejection, non-executing arithmetic verification, CONNECT proxy denial,
  private IPC lifecycle and an actual MCP stdio subprocess handshake.
  Installer checks include existing-key preservation/idempotency, refusal to
  replace a differently authorized key, symlink/hardlink rejection, multi-line
  entry rejection, checksum tampering and bundle path traversal.
  Four additional discovery regressions cover native-plus-launcher without a
  PATH alias, normalization of a bundled launcher on PATH, true conflicting
  installations, and an explicit discovery failure when the app is missing.
  Runtime-location coverage additionally checks selected apps outside Applications,
  hidden .app suffixes, spaces, disconnected-drive refusal to fall back, independent
  local Claude symlink discovery, stored per-agent settings, preserved project
  approvals, native executable/hash pinning, local CLI configuration, no demo
  directory creation when a selected runtime is absent, blocked peer configuration,
  offline diagnostic behavior, and app-only sandbox read grants on external volumes.
- `.venv/bin/python scripts/verify_ssh.py`: passed against an actual temporary
  `/usr/sbin/sshd` on 127.0.0.1 with dedicated generated keys, StrictModes,
  pinned host identity and generated restricted forced-command entry. Verified
  real task submission, identical acknowledgement replay, changed-content replay
  rejection, completed fixture result retrieval, exact Git commit/diff fetch,
  generic SSH command rejection, actual wrong host-key rejection and revocation.
  No model calls, system SSH edits or physical second computer involved.
  Repeated with a coordinator state path containing spaces after fixing
  ssh_config quoting for the macOS `Application Support` default path.
- `.venv/bin/python -m build --no-isolation`: current 0.2.0.dev5 wheel and source
  archive built from pinned tools. The package contains every implemented
  component and v1 schemas.
- `.venv/bin/python scripts/verify_wheel.py`: offline installation in a new venv
  outside this checkout; version, minimum Python, imports from installed wheel,
  doctor with no state creation, initialization and packaged MCP/schema checks.
- `.venv/bin/python -m pip check`: no broken requirements.

The tool runner's surrounding sandbox blocks socket binds and nested OS sandbox
launches. Socket/SSH/isolation/live checks were run with explicitly authorized
host execution so they test actual macOS behavior. This does not bypass the
restrictions applied to AABridge's managed worker. Ordinary prerequisite/build
checks ran without that exception.

## Real local Codex record

Installed Codex CLI: `0.163.0-alpha.11.2`; existing ChatGPT account authentication,
no API-key fallback or explicit costly model selection. The active Codex chat
used bridge CLI operations as coordinator; no person forwarded worker messages.
The worker had no shell, file-edit, browser, MCP or other model tools.

Evidence: [local-codex-2026-10-24.json](evidence/local-codex-2026-10-24.json).

- Task: `task-547d13489db4db9eb082d422`.
- Saved thread/session: `01a16b0b-5f82-7372-8242-b4e90ced774d`, unchanged for
  question, answer and revision.
- Question: “Should add perform integer addition?” It was persisted as
  `question-a4dfd602e57fa27ba3853a33`; the worker process exited while waiting.
- Answer: return `a + b`, including negative values and zero.
- Base: `2f0cb15c27585755657848013c36218ce2e9b20b`.
- First result: `a58e00449855c7d39a87f898458f1f61d87bbbc2`.
- Revised result: `3f0a31cd6db22dc98ee7d8f16875cb13a18ecaba`.
- Three model turns, revision 2, recorded active runtime about 13.46 seconds.
- Independently inspected final diff: replace subtraction with addition and add
  a one-line docstring, in `math_ops.py` only. The AST verifier independently
  returned 5 for (2,3), 1 for (-2,3) and 0 for (0,0), for both results.
- Original sample checkout stayed clean and unchanged. No push or merge.

Development attempts exposed an installed-protocol sandbox-field mismatch, a
question validator that incorrectly rejected an empty summary, transient provider
retry handling, and stale usage notifications after exact-session resume. Those
attempts failed without accepting results; fixes have deterministic regression
coverage. Installed schemas also confirm required thread/turn IDs on execution
notifications. The final stricter ID checks are covered by protocol fixture
checks; the recorded successful live task above is preserved rather than
rewritten as a fresh run after every subsequent hardening change.

Local demo state and task clones were preserved under
`/private/tmp/aabridge-live-os_wolmj`. The demo gate was disabled and its supervisor
stopped after testing. Provider transcripts/auth links stay in private local
state; they are not committed as evidence.

## SSH transport record

[loopback-ssh-2026-10-24.json](evidence/loopback-ssh-2026-10-24.json) records the
successful reproducible transport checks. The result was deliberately constructed
without a model to isolate transport behavior. Earlier transport checks exposed
an upload-pack strict-path error and loss of structured operation error codes;
both were fixed before the reproducible script passed. Test services were
stopped afterward; private disposable test state was retained for inspection.

## Permanent conversations and native task viewer, 25 October

Version `0.2.0.dev3` journals task messages and prepared input/context in schema 2.
Its 126-test suite covers message/state rollback together, retry deduplication,
question/answer/result preservation through revision and database reopen,
append-only guards, honest legacy migration, task pagination, peer/project
isolation, revocation and lossless large Unicode context paging. Existing real
IPC tests now read the journal after the full fixture workflow. Actual localhost
OpenSSH tests also list authorized tasks and retrieve their conversation.
The offline installed wheel exposes nine MCP tools, including both history reads.

The universal native app has a task browser, search/status filters, receiver
selection, conversation/context/event views, session identity and Markdown
export. Its bundled Python backend reads existing paired hosts without running
an agent. Native verification traversed that backend and real protected IPC,
then rendered the viewer using an explicitly labelled copy of the first captured
physical-test exchange. This is a UI/IPC verification fixture, not a second model
or physical mini run. Interactive Computer Use remains unavailable. The mini must
install the current app locally before these new RPC reads work there.

That history-viewer bundle was `dist/AABridge-Conversations-2026-10-25.zip`. No new model inference
was started during this persistence/viewer update. Storage details and upgrade
steps are in [conversation history](conversation-history.md).

## Native task chats and controls, 25 October

Version `0.2.0.dev4` adds human/agent sender and recipient labels, chat bubbles,
new approved tasks, answers, revisions and cancellation. Five-second refreshes
retain drafts and do not show recurring loaders. A private pending-send record
preserves the exact target, payload and retry key after app restart.

The 131-test suite covers GUI origin enforcement, malformed origins, project
authorization despite claimed labels, and authenticated operator identity kept
separate from task ownership. `scripts/verify_chat.py` drives the packaged native
model through the bundled backend and real authenticated IPC. It checks all four
mutations, silent refresh, draft preservation, restored pending text and retry
after deliberately dropping a committed submit acknowledgement. Exactly one task
is created by that retried submission. This explicit fixture never starts a
worker scheduler or model process. Interactive Computer Use remains unavailable.

The universal release is `dist/AABridge-Chat-2026-10-25.zip`. No model inference
was started during this update. Managed Claude/Gemini workers remain
unimplemented. Install the update locally on the mini before using its new chat
controls; full physical result/revision verification is still pending.
See [usage](chat-ui.md) and [release evidence](evidence/chat-release-2026-10-25.json).

## Editable scope and visible progress, 26 October

Version `0.2.0.dev5` adds local project/scope configuration, task scope selection,
new-file proposals, immediate pending chat bubbles and coarse worker activity.
The 140-test suite includes creation of an empty new file through the real
supervisor/IPC with an explicit deterministic worker fixture, review-required
journal results, original-folder preservation, non-execution of proposed text,
out-of-scope and symlink rejection, file-size bounds, idle-policy checks and
remote administrative-action denial. Provider-wire fixtures verify exact-session
activity and retry notifications without forwarding reasoning text.

The packaged native model loads the real local project backend, selects a task
scope subset, rejects an unapproved path, displays an immediate pending message,
reads active status, performs answer/revise/cancel/submit, and restores/retries a
send after a deliberately dropped acknowledgement without duplication. It also
preserves drafts and silent-refresh flags. Its own scope, pending-send and
activity views are rendered offscreen from clearly labelled no-model fixtures;
interactive desktop mouse/keyboard automation remains unavailable.

The final wheel installs offline and exposes its packaged CLI/MCP schemas.
Actual localhost restricted OpenSSH RPC, result transfer, host mismatch and
revocation checks pass without inference. No new physical model turn was run.
Read-only access to the mini's saved failed task confirmed an empty proposal and
a scope explanation, followed by demo acceptance failure; no requested file was
created. Updated mini deployment, local GUI project preflight and a live general
file-proposal round trip remain pending. The new profile verifies boundaries,
not application behavior, and requires review. Claude/Gemini workers are still
unimplemented.

Release: `dist/AABridge-Scope-2026-10-26.zip`. See
[setup and explanation](scopes-and-progress.md) and
[release evidence](evidence/scope-release-2026-10-26.json).

## First physical mini test, 25 October

The user reported **Receiver ready on this Mac** from the native app. The
MacBook then successfully performed pinned-key SSH bridge ping and project
discovery. The mini returned node `node-4491ad0a9ae6a601`, an enabled Codex demo,
and base commit `95a855381a9a1f0dfb4a3af845fdcdc3007d9f21`.

Task `task-5d0ab300953755a398455b77` started real Codex inference on the mini and
returned its requested integer-addition clarification. The MacBook delivered
the answer through the bridge, preserving session/thread
`01a173fe-0974-79e1-a279-009cb6867aa7`. The answer remained queued while the old
proxy shutdown waited for an active provider connection. The task was cancelled;
it later closed naturally after approximately 302 seconds, cleared its recorded
worker PID, and returned no result commit. No code fix, revision or Git result
transfer has passed on the physical mini yet.

Version `0.2.0.dev2` cancels owned proxy handlers before awaiting server closure.
This avoids the shutdown ordering problem described by Python's documented
[Server.wait_closed behavior](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.Server.wait_closed).
A regression test emulates the newer accepted-connection wait semantics. The
installer now refuses to upgrade active/closing tasks, then stops an idle owned
supervisor through local IPC before installing the wheel, so stale code is not
left running. Existing tasks, paths, peers and permissions are preserved. The
117-test suite and offline wheel smoke pass. The updated universal native app
is `dist/AABridge-Mac-Resume-Fix-2026-10-25.zip`; deployment on the mini and a
fresh full task test remain pending. See [first attempt evidence](evidence/physical-mini-first-attempt-2026-10-25.json).

## Physical mini pairing preparation (historical)

The user's mini was discovered through its advertised local SSH service; SSH
reachability passed and its Ed25519 host fingerprint was independently supplied
by the user, matched and pinned. Existing/dedicated client keys were not yet
authorized at the last authentication check. The user subsequently ran the
installer and reported a Codex installation/sign-in message. Its precise failure
and completed installation steps had not yet been inspected; no physical remote
model turn had occurred at this preparation stage. The local default state has the enrolled mini and a
private dedicated identity.

A reviewable `AABridge-Mini-Setup.zip` was prepared with only the built wheel,
installer scripts, coordinator public key, manifest and instructions. Its archive
contents, checksums, executable mode and exact installer source were checked.
In a disposable HOME, the real bootstrap installed its wheel offline, then
stopped on the expected missing Codex login before adding any SSH entry or
grant. Full authenticated setup on the physical mini remains pending diagnosis
of the existing Codex runtime bundled in its ChatGPT app. A separate Codex app
is not inherently required; the local live test used the ChatGPT-bundled
executable. See [pairing evidence](evidence/mini-pairing-2026-10-24.json).

On 25 October the installer ambiguity was reproduced against the MacBook's real
ChatGPT app, which contains both a native Codex image and a launcher named codex.
The corrected fallback selects the native image with PATH discovery disabled.
A dated replacement bundle was generated and audited at
`dist/AABridge-Mini-Setup-2026-10-25.zip`, with a distinct extracted folder.
This is local discovery evidence; the physical mini still needs its preflight.

After the SSD location clarification, the setup gained independently configurable
Codex/Claude paths and an editable runtime-paths.json. The current bundle is
`dist/AABridge-Mini-Setup-SSD-2026-10-25.zip`. Its selected worker path is a local
setup preference, not a hardcoded path in runtime code. Claude is independently
auto-discovered; its managed worker adapter remains unimplemented. The updated
wheel installs offline and its runtime CLI reports a selected unavailable path
without attempting authentication. Neither the actual SSD nor the mini's local
Claude installation has been inspected through an authenticated connection yet.

## Initial native receiver app and isolation diagnostics (historical)

The native app in `apps/desktop` compiles for arm64 and x86_64, with macOS 13 as
its deployment target. The final development app passed ad-hoc signature and
required-resource verification. Its `--verify-gui` path exercised the actual
background process, JSON path selections, bounded streaming output and error
completion, without installing the receiver, granting access or running a model.
The SSD path correctly reports unavailable on this MacBook, and the six actual
isolation canaries pass. Desktop Computer Use permissions are unavailable here,
so interactive file-picker and window behavior have not been manually verified.
The app's own view was rendered offscreen for inspection; this is not a desktop
screenshot or evidence of a physical mini test.

A real disposable copy of the Python framework was relocated outside Homebrew.
With the previous read grants, its sandboxed interpreter failed to load a library
and returned no canary results. With the revised probe-only installation read
grant, all six checks passed. The provider worker profile receives no extra
Python grant. See [recorded portability evidence](evidence/isolation-portability-2026-10-25.json).
The physical mini's reported generic failure could have this cause or another;
its actual result remains pending the updated diagnostic run.

That initial GUI bundle was `dist/AABridge-Mac-Preview-2026-10-25.zip`. Its receiver
setup payload contained only the coordinator's public key and preserved the
existing restricted-grant preflight. A task activity dashboard was pending then;
the current version above implements it.
The offline wheel smoke test and dependency check also pass. No model tests were
started during this GUI/isolation update.

## Foundation environment history

Python 3.11.17 was installed through Homebrew, separately from Apple's Python
3.9.6 at `/usr/bin/python3`. A marked block in the newly created `~/.zprofile`
selects Homebrew Python 3.11 for new login shells. `.venv` uses Python 3.11.
Initial foundation checks had 37 passing tests and an offline 0.1.0.dev1 wheel
smoke test; those are historical evidence superseded by the checks above.

This checkout remains an uncommitted local monorepo on branch main, without a
Git remote, push or deployment. Commits described above exist only in assigned
disposable demo/task repositories. No Claude tests have begun. Follow
[two-device.md](two-device.md) for the genuinely outstanding physical test.
