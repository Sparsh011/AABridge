# AABridge

## Team collaboration preview

The 0.3.0.dev4 build connects paired Macs directly with mutual TLS. Create and
exchange cards in **Devices**; macOS Remote Login is no longer required for new
connections. Both Macs still need a reachable LAN or VPN address. Update both
receivers and exchange new cards to upgrade an existing SSH pair. See
[direct connection setup](docs/two-device.md#direct-app-connection-current-desktop).

The 0.3.0.dev1 team build adds bidirectional Mac roles, admin-selected Codex/Claude leaders and followers, a shared team chat, full-project coding branches, and 20% larger text. Start with the [team setup guide](docs/team-collaboration.md). The older proposal-mode documentation below remains available for existing projects.

A Python CLI and local supervisor, with a native macOS task/conversation viewer and receiver setup app, for delegating bounded tasks between
coding-agent sessions on one Mac or over SSH. Applications and reusable packages
have separate directories in this monorepo. There is no web dashboard or Node
server, and no database server to install; Python's SQLite stores local state.

**Current slice:** working Codex and Claude Code proposal workers, durable tasks, question/answer
checkpoints, exact-session resumption, revision requests, cancellation, MCP
stdio tools, protected local IPC, and restricted pinned-key SSH RPC/Git transfer.
A real local Codex question → answer → commit → revision test passed. Real
OpenSSH transport tests passed on localhost. Physical MacBook–Mac mini SSH and a
real Codex question passed; a full result test awaits the receiver cleanup update.
A [Claude Code worker](docs/claude-worker.md) runs the same proposal flow in the same
sandbox, using a Claude subscription token. Its real-binary sandbox and exact-resume
checks pass against a local fixture; a live subscription task is still pending.
The Gemini worker adapter remains unimplemented.

Workers propose complete contents for approved text files; the supervisor checks
scope without executing repository code. The native **Projects & scope** editor
registers existing or new file paths on the receiving Mac. General file proposals
require human review; the separate math demo retains its fixed addition tests.
See [scope setup and progress](docs/scopes-and-progress.md). General shell/build
execution is not enabled.
See [limitations and threat model](docs/threat-model.md) before enabling a worker.

## Install

Use the [native task chat](docs/chat-ui.md) to start approved tasks, answer questions,
request revisions and cancel selected work, with labelled human/agent messages.
It also browses local/remote tasks, supplied context, events and exports with
quiet background refresh. Its [receiver setup](docs/desktop.md)
also provides agent file pickers, diagnostics and installation progress. Viewing
does not start model inference.

Requires macOS, Python 3.11+, Git, OpenSSH, and an installed Codex CLI signed in
with a ChatGPT account on the worker machine, or Claude Code with a Claude
subscription for [Claude projects](docs/claude-worker.md). Neither needs the other. Python's virtual environment is dependency isolation, not the
worker security boundary.

From the repository root:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/aabridge doctor
```

Python 3.11 was installed separately through Homebrew on the development Mac;
Apple's Python was preserved. Other Macs need their own compatible interpreter.
The CLI uses only the Python standard library at runtime.

## Try a real local task

This uses existing Codex subscription usage. `project enable` checks isolation,
account authentication and effective restrictions without starting model inference;
`task submit` starts a model turn. The demo folder must not already exist.

```sh
.venv/bin/aabridge init
.venv/bin/aabridge demo-init --directory "$HOME/aabridge-demo" --project demo
.venv/bin/aabridge project enable --id demo
.venv/bin/aabridge start
.venv/bin/aabridge task submit --project demo --key first-demo \
  --objective 'Fix add. First ask whether it should perform integer addition; after the answer, propose the corrected complete file.'
```

Save the returned task ID. `task wait --id TASK_ID` returns within 25 seconds;
repeat it if still running. A question includes its ID and revision:

```sh
.venv/bin/aabridge task answer --id TASK_ID --revision 1 \
  --question-id QUESTION_ID --key first-answer --text 'Yes, return a + b.'
.venv/bin/aabridge task wait --id TASK_ID
.venv/bin/aabridge task revise --id TASK_ID --revision 1 --key first-revision \
  --text 'Add a concise docstring explaining that add returns the sum of two integers.'
.venv/bin/aabridge task wait --id TASK_ID
.venv/bin/aabridge task events --id TASK_ID --cursor 0
.venv/bin/aabridge stop
```

`TASK_ID` and `QUESTION_ID` are placeholders for returned values. The source
repository is unchanged; each task receives an isolated Git clone. Results name
the base/result commits, task checkout, changed paths and verifier outcomes.
Nothing is pushed or merged. The default state directory is
`~/Library/Application Support/AABridge`; every command supports `--state-dir`
for a different private absolute path. Use a short path if the Unix socket path
exceeds macOS's limit. No background service is installed at login.

For a plain-language account of everything implemented, the reasons for each
component, sharing requirements and current limits, read
[the implementation overview](docs/implementation-overview.md).

For autonomous use, connect an active coordinator through [MCP](docs/mcp.md).
It can answer questions and request revisions itself, without human forwarding.
An idle desktop chat cannot be woken by MCP alone.

## Two computers

Follow the [MacBook–Mac mini setup guide](docs/two-device.md). It starts with
finding the mini's address in System Settings and covers trusted host identity,
dedicated keys, explicit project grants, remote tasks and Git result review.
No existing SSH knowledge is assumed for the first setup step.

Apps can live on internal storage or an explicitly selected external SSD.
See [independent runtime locations](docs/runtime-locations.md) for per-agent
selection, local Claude discovery and disconnected-drive behavior.

## Check and build

Build the universal macOS app with one command after installing the
development dependencies and Apple's command-line tools:

```sh
make build
```

Each successful build replaces `build/AABridge.app`, which is Git-ignored.
No ZIPs or numbered output folders are created. Override the location with
`make build APP_PATH="build/My Apps/AABridge.app"`. It uses the existing
local device public key for the setup payload; see [desktop build options](docs/desktop.md#build)
to supply another public key. Run `make check` for tests alone.

Individual checks and Python packaging commands:

```sh
.venv/bin/python scripts/check.py
.venv/bin/python scripts/verify_ssh.py
.venv/bin/python scripts/verify_claude.py
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/verify_wheel.py
.venv/bin/python -m pip check
```

The first command runs deterministic tests, including test-only worker fixtures
and real Unix socket/proxy/MCP subprocess checks; it makes no model calls.
`verify_ssh.py` is an opt-in real localhost SSH/Git test with a constructed result
fixture, no model calls and no system SSH configuration edits. It retains its
private disposable test state and stops its owned services. It needs permission
to bind local sockets and run an unprivileged `/usr/sbin/sshd`.

Build tools are pinned in `requirements-dev.txt`. The wheel smoke test installs
the current version offline in a new environment outside the source checkout.
Byte-for-byte reproducible archives are not claimed. See [test evidence](docs/verification.md).

## Layout

```text
apps/
  cli/                 Operator CLI and task operations
  daemon/              Durable worker supervisor
  mcp-server/          Tools for active coordinators
  installer/           Local Mac worker setup from an offline pairing bundle
packages/
  protocol/            Validation and packaged v1 JSON schemas
  core/                Local policy, authentication, SQLite tasks/events
  workspace/           Isolated Git clones and restricted AST verification
  transport-local/     Private authenticated Unix socket client
  transport-ssh/       Pinned SSH transport
  adapter-codex/       Codex app-server worker and macOS restrictions
  adapter-claude/      Claude Code print-mode worker (same restrictions)
  preflight/           Read-only prerequisite diagnostics
scripts/               Test and packaging helpers
examples/              Non-secret configuration examples
tests/                 Deterministic tests
docs/                  Design, setup, security and evidence
```

See [architecture](docs/architecture.md), [milestones](docs/milestones.md),
[agent compatibility](docs/agent-compatibility.md), and
[troubleshooting/removal](docs/operations.md). The full original brief is in
[specification](docs/specification.md); this milestone does not complete it.
