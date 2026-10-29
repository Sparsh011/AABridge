# Active coordinators and MCP

Start the local supervisor before task operations. The MCP process is a small
stdio facade; ending it does not stop tasks. It can route requests to this host
or a locally enrolled remote. It cannot inject text into existing desktop
sessions or awaken an idle conversation.

The saved remote chooses its transport: new connection cards use direct mutual
TLS; older SSH enrollments keep SSH until explicitly upgraded with new cards.
MCP tool names, request bodies, ownership and project grants are unchanged. The
facade does not expose listener setup, pairing or certificate replacement as
remote tools. Direct results are fetched over TLS by the admin's team runner.

## Codex coordinator

Use the absolute installed executable and state path. From this checkout:

```sh
codex mcp add aabridge -- "$PWD/.venv/bin/aabridge" mcp \
  --state-dir "$HOME/Library/Application Support/AABridge"
```

This changes the coordinator's MCP configuration; it is an instruction for the
operator, not something the setup has silently installed. Refresh/start the
coordinator as required by the host. Inspect with `codex mcp list`; remove with
`codex mcp remove aabridge`. The syntax was checked against this Mac's installed
CLI and [official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp).

## Claude Code coordinator

After Claude Code is installed and the user has signed in locally, the analogous
stdio setup is:

```sh
claude mcp add --transport stdio --scope user aabridge -- \
  /absolute/path/to/aabridge mcp --state-dir /absolute/private/state
```

The example follows [Claude's MCP documentation](https://code.claude.com/docs/en/mcp).
It has not been executed as a live coordinator yet. Connecting Claude as an
active MCP coordinator is independent of the [managed Claude Code
worker](claude-worker.md); either agent can coordinate either worker.

## Tools and suggested coordinator instruction

Available tools: `bridge_projects`, `bridge_submit`, `bridge_status`,
`bridge_events`, `bridge_tasks`, `bridge_conversation`, `bridge_answer`,
`bridge_revise`, `bridge_cancel`. Task-list and conversation reads are paginated
and do not start inference. Each tool has
a validated `body`; mutations additionally require a stable identifier `key`.
Use the same key/body when retrying an uncertain acknowledgement. Optional
`remote` names an already enrolled local remote, never a hostname or command.
There are no enrollment, execution-enable, shell, shutdown, push or merge tools.

Agent coordinators can include `origin: {"kind": "agent", "name": "Codex coordinator"}`
in submit/answer/revise bodies so the chat UI can identify them. Preserve that
exact body on retries. This is display metadata, never a source of permissions
or proof of human approval. Desktop-originated messages use a human label.

Suggested instruction for an active agent:

> List assigned bridge projects. Submit one demo task using that project's exact
> baseline, scope, limits and proposal-v1 profile. Ask the worker to fix add and
> first clarify whether integer addition is intended. Retain the task ID, poll
> status/events with a cursor, and answer its exact question/revision that it
> should return a + b. Review the independently verified result. Request one
> revision adding a concise docstring, then review the second result. Use stable
> keys for each mutation. Do not change policy, publish changes or forward secrets.

A local CLI coordinator completed that real model round trip during development.
The MCP stdio handshake/tool schemas and its connection to real protected IPC
were tested separately. A live model coordinator connected through the installed
MCP configuration has not yet been demonstrated; these are distinct evidence.

The managed worker has MCP/plugins and every other model tool disabled. It
cannot become another coordinator or recursively delegate.
