# Protocol v1

Schemas ship in `aabridge_protocol/schemas/v1/`: request, task, event and result.
They describe structural contracts. The standard-library runtime validator adds
UTF-8/message limits, duplicate-key/control checks, normalized path checks,
authentication, grants, revision/session state and locally authorized ceilings.
Passing a JSON schema does not grant authority. MCP tool body schemas are read
from the same packaged request schema; the runtime still validates every call.

A request is a bounded newline-terminated JSON envelope with `version: 1`, `op`,
`key` and `body`. Sender identity comes from authenticated local IPC, direct TLS or legacy SSH, never
an envelope field. Operations: submit, status, events, tasks, conversation, answer, revise, cancel,
ping, projects and local-only shutdown. Replies contain either `result` or a
safe structured `error` with code and message. A well-formed remote exchange
can return an operation error without being a failed SSH connection.

Direct TLS uses a bounded newline-terminated outer envelope defined by
`direct-envelope.schema.json`: `version: 1`, `kind: rpc`, and the unchanged
`request` object; or `kind: result_bundle`, `task_id`, and an exact `commit` SHA.
The entire request is at most 131,072 bytes. Replies use the same result/error
shape, at most 262,144 bytes. Bundle replies carry size and SHA-256 metadata,
followed by exactly that many binary bytes (at most 64 MiB). Only completed
results owned by this authenticated peer with current project grants are
exported. There is no caller-supplied path or executable.

New public cards follow `connection-card.schema.json` (version 2, transport tls).
They carry device ID, display name, host, port, one public X.509 certificate and
its SHA-256 fingerprint. Runtime validation additionally checks certificate
parsing, matching pins, duplicate identities, existing enrollment, and revocation.
TLS validates the paired certificate chain and both ends compare the exact
certificate fingerprint. Enrollment and listener settings are local operator
actions, never remotely invokable task or MCP operations.

Submission names a project, registered recipient, exact baseline, bounded
objective/scope/acceptance/context, limits and `permission_profile: proposal-v1`.
The supervisor assigns task/attempt IDs, sender/receiving-node identity,
revision, timestamps and durable status. Context is bounded literal text,
not a path to fetch or authority to read more files. A task ID is not a credential.

Task-history reads (`tasks`, `status`, `conversation`, `events`, `activity`)
accept optional boolean `body.shared`. Omitting it retains owned-only peer
reads. When true, a receiver may also expose tasks whose sender is `local`,
whose stored permission profile is `coding-v1`, and whose current coding
project is granted to the authenticated peer. Other peers' tasks and proposal
tasks stay private. Grants and revocation are rechecked for each read/page.
Task lists advertise `capabilities.shared_task_history`; summaries and shared
status carry `permissions.can_control`. That response metadata does not grant
authority: mutations still require the owner or local admin, and reject a
`shared` body field. Current request and task schemas describe these additions.

Submit, answer and revise may include `origin: {kind, name}`, where kind is
`human` or `agent`. This labels the conversation participant; it grants no
permissions and is not a verified personal identity. The desktop supplies
`human` / `You`. Journal records keep the authenticated requesting identity
separate from the task owner and the displayed actor/recipient. Older records
without these fields retain a generic coordinator label.

Every mutation uses a per-sender idempotency key. Reuse the exact key/body when
retrying after a lost acknowledgement. The original acknowledgement is returned
on replay; use status for current state. Changed-content key reuse fails.
Answer requires current question ID and revision. Revision requires a completed
result and remaining revision budget, increments revision and creates a new
attempt while keeping the same provider thread and task checkout.

Events include version, task/attempt/revision/status/time and a returned ordered
cursor/kind. Request events with cursor 0 initially, then the last returned
cursor; pages contain at most 64. Retrieve current question/result via status.
The supervisor owns worker lifetime; RPC disconnection is not cancellation.

`tasks` takes a numeric `cursor` (initially 0) and lists up to 32 authorized task
summaries, newest first. Its cursor pages backwards. `conversation` takes
`task_id` and `cursor` (initially 0) and returns ordered permanent message parts,
with ascending cursors and `has_more`. Pages are limited to 32 parts and a bounded
encoded size. Large context records carry group IDs and numbered parts so text
can be reassembled without truncation. These reads enforce existing task-owner,
project-grant and revocation checks. Unknown operations on old receivers fail
explicitly; the desktop viewer requests an update in that case.

Only locally configured immutable project/runtime/path mappings are execution
authority. Neither tasks nor tool calls can select executables, provider
credentials, local folders, enrollment changes, generic commands or SSH options.
Resource limits bound runtime, turns and revisions, not guaranteed currency cost.

The local project editor may register/update an idle file-proposal project;
that operator action is not an RPC operation. Tasks can select a subset of the
receiver-approved files. Runtime `status` can contain coarse `activity`
(`phase`, `message`, `time`) and the locally chosen `verification_profile`.
Activity comes from supervisor stages and validated provider notification kinds,
never provider reasoning text. It is a last reported stage, not a heartbeat.

The math demo keeps `integer-function-v1`. `file-proposal-v1` permits existing or
new approved text paths, checks scope and Git diff without running code, and
returns `review_required: true` in its verification entry. Its journal message
kind is `review_required_result`; it does not claim passing application tests.
