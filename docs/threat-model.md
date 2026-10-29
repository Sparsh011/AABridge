# Threat model and supported boundary

Task text, peer requests, repositories and worker proposals are untrusted.
Local operator policy determines authority. Claims of urgency, human approval
or emergency in a prompt cannot expand permissions. The supported execution
slice is `proposal-v1`, not arbitrary unattended repository execution.

## Trusted components and assets

For new direct connections, the supervisor owns a TLS listener enabled by a
local connection-card action. Both ends validate explicitly paired certificates
and compare their exact SHA-256 pins; remote identity is derived from that
certificate, never from a request's claimed sender or a local-admin token.
Project grants, task ownership and revocation are rechecked for every request.
Pairing/replacement remains a trusted local operator action. Public cards must
be exchanged through a trusted channel; TLS cannot correct a substituted card.
There is no generic shell, filesystem-download or remote administration endpoint.
The system OpenSSL executable is trusted for one-time app identity generation.
Private app keys stay 0600 in bridge state and are never exported in cards.

Network connection and request-read/write timeouts and message/connection limits
bound ordinary failures; this is not protection against network denial of
service or malicious same-user processes. Result bundle transfer is capped at
64 MiB after generation and may temporarily consume more disk while Git creates
the export. No relay/NAT traversal is implemented. Native coding still uses the
signed-in user's tool permissions as described in team-collaboration.md; the
proposal sandbox described below does not apply to native coding projects.

The local operator/account, OS, Python, system Git/OpenSSH, installed Codex
harness and its signed app resources are trusted. Assets include unrelated
files, source, provider credentials, bridge tokens/policy, task/session identities
and result history. Other OS users and enrolled malicious peers are threats.
Same-user malicious processes, root/kernel compromise and exploitation of
trusted OS/harness services are outside the proven boundary.

## Enforced controls

| Threat | Implemented control |
| --- | --- |
| Unenrolled client or task-ID impersonation | Private state/socket, random local credentials, peer-specific credentials and project/task ownership checks |
| Remote MITM | Mutual TLS with exact paired certificate pins; legacy SSH uses a dedicated key and explicitly verified pinned host key; no automatic identity replacement |
| Remote shell/policy escalation | Fixed validated direct RPC; legacy SSH forced key command with `restrict`; administrative commands remain local; no generic exec tool |
| Replay, stale answer, cross-task injection | Transactional content-bound idempotency, revision/question/attempt IDs, exact saved thread binding and ordered event cursors |
| Unsafe paths and state | Narrow relative scope; local project mapping; canonical/private ancestor checks; no-follow state/file opens; hardlink checks; committed symlinks/submodules rejected |
| Malicious proposal code | No model tools; structured output; independent scope validation; demo AST interpretation or review-required file proposals; no repository scripts executed |
| Worker deletion or privilege escalation | Whole-process file-write and executable-child restrictions below; no sudo or approval bypass; broker writes only validated registered file |
| Failure and cancellation | Time/turn/revision limits; one worker; owned process-group termination; partial work retained; conservative restart/reconciliation |

## Actual macOS process restrictions

The provider process runs under `/usr/bin/sandbox-exec`. Its profile **allows
platform operations by default**, then explicitly denies file content reads
outside enumerated runtime/system roots and the exact local auth file; writes
outside its dedicated worker runtime; execution of other binaries and fork;
privileged Mach host ports; signalling other processes; inbound network/binds;
unrelated preference domains; and network egress except syslog and its private
localhost proxy port. This is not a deny-all desktop or IPC sandbox.

Trusted read roots include the installed app bundle, Homebrew runtime,
System/framework/runtime libraries, system preferences/managed preferences and
system keychain resources. They are broader than just a source file. The harness
must read its own local Codex credential to authenticate. A private per-worker
symlink points to that existing credential; bridge messages do not carry it.
The bridge checks metadata and the harness reads credential contents. Do not
claim credential confidentiality from a compromised Codex harness.

Direct worker network access is denied. A per-worker authenticated loopback
CONNECT relay permits only `chatgpt.com:443`; upstream addresses must resolve to
public IPv4 addresses. TLS remains end-to-end; the relay does not decrypt or log
contents. Connection count, tunnel bytes and lifetime are bounded. A provider
connection remains an information-flow channel: scoped source is deliberately
sent to that provider. Same-user processes can inspect environment/state and
are not isolated by this design. IPC/OS services remain trusted, and indirect
IPC-mediated escape resistance has not been exhaustively demonstrated.

Before enabling a project, disposable non-model probes verify denied outside
read, write, deletion, direct network connection and child execution, plus allowed
runtime writes. Then sandboxed app-server startup must confirm ChatGPT account
authentication and disabled tools. Every daemon also runs isolation probes before
its first task. A changed Codex binary hash, unsupported operation or failed
preflight stops execution. This demonstrates those concrete controls on the
tested macOS/CLI version, not a formal or comprehensive sandbox proof.

## Limits that remain

- `integer-function-v1` independently checks the math demo. Locally approved
  `file-proposal-v1` permits existing/new scoped text files but checks only file
  boundaries and Git diff; its results require human review and do not claim
  application correctness. General shells,
  file tools, repository test scripts, arbitrary language builds and dependency
  installs stay unavailable; a writable clone or clean diff would not make them safe.
- No claim of protecting all data against a compromised same-user process,
  provider harness, trusted system service or kernel. Stronger OS/account/VM
  isolation is needed before broad hostile-code execution.
- Filesystem checks do not defeat malicious same-user races or certify every
  macOS ACL. Local configuration updates are atomic but simultaneous operator
  edits are not a multi-writer policy workflow.
- Runtime/turn limits are operational bounds, not guaranteed currency spending
  caps. Existing ChatGPT login is required; API fallback is disabled.
- Sources/context and summaries can contain sensitive text. No universal secret
  detector is claimed. Use dedicated non-secret repositories; provider session
  raw provider transcripts remain private locally and are not forwarded as bridge
  logs. Schema-2 bridge messages and supplied context are intentionally readable
  by the local operator and the authorized task-owning peer; history uses the
  same project grants and revocation checks. It does not expose provider
  credentials or internal model reasoning. Exported messages/context can contain
  user-supplied sensitive text and should be handled as project data.
- No disk quota or rotating retention policy is implemented. Task count is
  capped at 256, payload/source/event sizes are bounded, but provider runtime
  files and Git metadata can consume disk. No automatic deletion is provided.
- Cancellation cannot reverse a completed commit. Replayed acknowledgement does
  not imply exactly-once arbitrary side effects. Restart reconciliation is local.
- SSH is not discovery or NAT traversal. Physical two-device execution, a live
  subscription-backed Claude Code task and Gemini worker behavior remain unverified.
- The Claude Code worker receives its subscription token in its environment. The
  token is stored 0600 in bridge state, never sent over RPC/MCP, and the relay
  only reaches api.anthropic.com. A compromised Claude Code binary could misuse it,
  as a compromised Codex binary could misuse its auth file.

There are no push, merge, deploy, bulk cleanup, recursive delegation or automatic
policy expansion operations. Expanding capabilities requires an implemented and
locally authorized profile with independent verification, not model consent.
The native project editor is a local operator action. It cannot configure a
remote receiver through RPC. Scope changes require idle project tasks, retain
the Codex runtime pin, and disable execution until preflight succeeds. New file
creation is restricted to approved paths in disposable task checkouts. Scope
validation does not make executing those proposed files safe.

The assisted macOS installer is a local operator action, outside peer/model RPC.
Running the reviewed bundle grants its included public key only the demo project
after Codex preflight. It appends the generated restricted authorized_keys entry
without replacing existing keys and refuses existing mismatched/revoked grants.
The bundle contains no private key or provider credential. Transfer authenticity
comes from the trusted local transfer channel and operator review; an unsigned
checksum manifest alone does not establish authenticity.
