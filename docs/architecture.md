# Architecture

AABridge is one installable Python distribution assembled from separated
application and library directories. Python 3.11 supplies argparse, asyncio,
sqlite3, JSON, subprocess and file primitives. SQLite is an embedded local
transactional database; peers call structured operations rather than accessing
its files. No Node, public service, account system or database server is needed.

## Per-host ownership

Each host runs a supervisor owning its task database, private socket, dedicated
worker processes and task clones. Submit acknowledges after committing durable
intent and returns promptly. Tasks survive client/SSH/MCP disconnects. Mutations
use per-identity keys and canonical content digests: identical retries return
the original acknowledgement; changed-content retries fail. Query status for
the latest state rather than expecting a replayed acknowledgement to update.

Authorization precedes cache lookup. Enrolled identities have explicit project
grants and may access their own tasks; the local operator can inspect all tasks.
One worker runs at a time in this slice. Questions checkpoint the task and end
the worker process; an authorized answer resumes the exact saved Codex thread.
Revisions keep the thread and task checkout but get a new attempt ID/revision.
Thread reuse across different tasks is rejected. Ordered events have resumable
cursors; retrieval returns at most 64 events at a time.

A supervisor restart marks uncertain running/cancelling attempts interrupted,
blocks new work for the affected project and requires local reconciliation. It
never guesses whether a previously running model process stopped safely.

## Transport and MCP

Local IPC uses a mode-0600 Unix socket inside a mode-0700 state directory, plus
an unpredictable per-identity token. Files are checked for ownership, type,
permissions and unsafe links. Tokens are not task IDs and are not model context.

New device cards use an app-owned direct TLS listener with mutually authenticated,
explicitly pinned certificates. The listener is enabled by creating a card and
shares the local supervisor's request validator and Store, using the enrolled
certificate's peer identity. It reloads trust on handshakes and policy on every
request. Result bundles are authorized through task status/ownership before
export, checksummed in transit and imported into private Git review repos with
exact-commit verification. No local admin token is sent over the network.

Legacy SSH uses a dedicated Ed25519 client key, an explicitly verified pinned host key,
non-interactive fixed RPC command, and a restricted forced-command key entry.
Remote input cannot choose local paths, installed binaries, commands or policy.
Both transports require network routing/LAN/VPN availability. Direct TLS does
not start or configure the system SSH server. See the direct-connection section
of team-collaboration.md for migration and integration limits.

The small stdio MCP facade implements initialize, ping, tools/list and tools/call
using JSON-RPC and the same validation/transport. It exposes project discovery,
submit, status, events, answer, revise and cancel, with full body schemas. It
has no administrative tools, streaming HTTP, OAuth server, SDK dependency or
claim of broad MCP conformance certification. An active local coordinator can
route to an enrolled remote by name. MCP does not awaken idle desktop chats.

## Managed worker and verifier

Only Codex is implemented. The adapter uses installed `codex app-server` over
stdio, checks ChatGPT account authentication and effective disabled features,
and starts/resumes dedicated threads. Installed protocol JSON schemas were
checked because this alpha CLI differs from current web examples. Explicit
retryable provider events remain within the original task deadline; terminal
failures are surfaced without raw provider diagnostics.

A fresh isolated app-server process runs each turn, with its own CODEX_HOME,
minimal environment, no API keys and no coordinator connectors. All model tools
are disabled; requests for permission or unsupported tools fail closed. The
model receives bounded source text, not a mounted repository, and returns a
structured question or proposed replacement contents. See the exact OS boundary
and its limits in [threat-model.md](threat-model.md).

The supervisor independently checks proposals, interprets a deliberately small
integer-expression AST, writes only the registered file, commits in the task's
detached clone and checks the resulting diff and clean status. It never imports,
compiles or executes proposed repository code. There are no arbitrary build/test
scripts, package installs or general shell edits in this profile.

## Source and results

A local operator registers an existing local Git repository, exact baseline and
one-file verification profile. Tasks cannot change that baseline or select a
remote URL. Clone uses Git transport without local hardlinks and rejects
committed symlinks/submodules. The active source checkout is never edited.

For remote execution the approved baseline must already exist on the worker
host, transferred/registered by the operator. Automatic source publishing from
the coordinator is not implemented. Result commits can be fetched with Git over
the same restricted SSH key into a separate bare review repository, with exact
SHA and bounded diff checks. The gateway maps project/task IDs to local metadata;
it never executes a received Git command or received filesystem path.

Host, agent runtime and model are separate concerns. Adding Claude, Gemini or
another provider requires a separate adapter with authenticated start, exact
resume, cancellation, restricted capabilities and its own live evidence. A
provider name in a task does not install or authorize an adapter. Reverse
routing uses the same enrollment mechanism; recursive worker delegation is
unavailable because worker tools are disabled.
