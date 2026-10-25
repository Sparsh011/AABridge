# Claude Code worker

AABridge can run Claude Code as a managed proposal worker, alongside Codex. The
worker is chosen per project on the receiving Mac; the task protocol, SSH
transport, questions, exact-session resumption, revisions and result review are
the same for both agents. The worker runs on the receiving Mac (for example the
Mac mini); the sending Mac (for example the MacBook) only needs the updated
AABridge app or CLI to pick a Claude project.

## Install the update

The release is `dist/AABridge-Claude-2026-10-26.zip` (version `0.2.0.dev7`). It
is built locally and is not committed (`dist/` is ignored). Install it the same
way as earlier releases, on **both** Macs:

1. Copy the zip to each Mac (AirDrop works) and unzip it.
2. Quit any open AABridge window, then replace the old `AABridge.app` with the
   new one and open it.
3. On the receiving Mac (the mini), choose the **Worker agent** in Receiver
   setup (Codex or Claude Code), then select **Install receiver**. For Claude
   Code, first run `claude setup-token` in Terminal and paste its token into the
   token field (not needed again once stored). Install receiver reinstalls the
   bundled wheel over the existing install and keeps your pairing, SSH grant
   and task history. It refuses to run while a task is still active. Codex
   uses the `demo` project. Claude Code gets a separate `claude-demo` project,
   and the existing MacBook grant is extended to it. Run it once per worker to
   set up both.
4. On the sending Mac (the MacBook), the new app only needs to be opened. Use
   **Refresh** in the task chat to reload the receiver's projects.

To rebuild the zip from source, run `python -m build --no-isolation`, then
`scripts/make_pairing_bundle.py` and `scripts/build_desktop.py` as described in
[desktop.md](desktop.md).

### Why Claude needs a token and Codex does not

Codex keeps its ChatGPT login in a file (`~/.codex/auth.json`), and the sandbox
can grant read access to exactly that file. Claude Code keeps its login in the
macOS Keychain. The sandbox deliberately cannot open the Keychain, which also
holds unrelated secrets, and that login is short-lived and refreshed back into
the Keychain. `claude setup-token` creates a long-lived, inference-only token
for headless use, which the worker receives as an environment variable.

## How a turn runs

Each turn starts one fresh `claude -p` process with stream-JSON output inside the
same whole-process macOS sandbox as Codex:

- All built-in tools (`--tools ""`), MCP servers (`--strict-mcp-config` with an
  empty config), slash commands, hooks, CLAUDE.md discovery and auto-memory are
  disabled. Claude Code's system prompt is replaced by the AABridge proposal
  instructions, and `--json-schema` enforces the same proposal schema as Codex.
- The supervisor checks the session's reported effective tools. Anything other
  than the synthetic `StructuredOutput` tool, any MCP server, any tool call
  other than `StructuredOutput`, or any permission prompt fails the turn as
  `permission_denied`. Claude Code also sends a reserved no-op
  `DeferredToolPlaceholder` name to the API; calling it would be rejected the
  same way.
- The first turn passes a supervisor-chosen UUID with `--session-id`. Later
  turns use `--resume` with only that saved ID. Events from any other session
  are rejected.
- Only the per-task runtime directory is writable. Reads are limited to system
  roots, the Claude Code installation and the system timezone database. The
  project, bridge state, keychain and other tasks are not readable. Child
  processes (`git`, `security`, `ps`, ripgrep) are denied.
- Network access goes only through a per-worker authenticated CONNECT relay
  that accepts `api.anthropic.com:443` and nothing else. TLS stays end to end.
- Authentication is a Claude **subscription** token from `claude setup-token`,
  passed as `CLAUDE_CODE_OAUTH_TOKEN`. API keys are refused when stored, never
  inherited from the environment, and a session reporting an API-key source is
  rejected, so there is no API-billing fallback. The sandbox cannot read the
  keychain, so a different login stored there is never used.
- The binary's SHA-256 is pinned when the project is enabled. Claude Code
  updates itself often. After an update, tasks fail with `adapter_changed` until
  you run the enable step again, which re-pins the current binary.

## Set up a Claude project on the receiving Mac

1. Install Claude Code and sign in with your Claude subscription. The CLI that
   ships with the Claude desktop app is also discovered under
   `~/Library/Application Support/Claude/claude-code/`. If several versions are
   there, pick one explicitly:

   ```sh
   .venv/bin/aabridge runtime list
   .venv/bin/aabridge runtime set --agent claude --path "$HOME/.local/bin/claude"
   ```

2. Create a long-lived subscription token and store it in AABridge. The token
   goes in through a hidden prompt (or stdin), never on the command line:

   ```sh
   claude setup-token
   .venv/bin/aabridge claude-token set
   ```

   The token is saved as `claude-oauth-token` (mode 0600) in the AABridge state
   folder. It never leaves this Mac. `aabridge claude-token remove` deletes it.

3. Register and enable a project with `--agent claude`. Enabling runs the
   isolation probes and starts Claude Code in the sandbox. It makes no network
   call and runs no model turn:

   ```sh
   .venv/bin/aabridge project register --agent claude --id backend \
     --repository /path/to/repo --verification examples/file-project-verification.json
   .venv/bin/aabridge project enable --id backend
   ```

   For a disposable arithmetic demo: `aabridge demo-init --agent claude
   --directory "$HOME/aabridge-claude-demo" --project claude-demo`.

   In the desktop app, open **Projects & scope** and choose **Claude Code** as
   the worker agent. Paste the `claude setup-token` token there the first time;
   the save step stores it and enables the project. The agent of an existing
   project cannot change. Create a new project ID for the other agent.

4. Grant remote senders access as usual (`peer grant`, or the sender toggles in
   Projects & scope).

## Use it from the other Mac

Refresh the receiver in the task chat. Claude projects appear as
`project · Claude` and start, answer, revise and cancel exactly like Codex
projects. From the CLI, `task submit --project backend` reads the project's
worker from the receiver, so the recipient is set automatically.

## Verification

- `tests/unit/test_claude.py`: deterministic stream-protocol, permission,
  session-pinning, token, discovery and supervisor-routing tests.
- `scripts/verify_claude.py`: opt-in check that runs the **real installed**
  Claude Code binary in the **real** sandbox against a local fake Messages API.
  It covers the sandbox preflight, a question turn and an exact-resume answer
  turn. It makes no model calls and uses no subscription.

A live, subscription-backed task has not been run as part of this change. It
needs your own `claude setup-token` token and uses your subscription. Run one
bounded demo task (as in the README, with `--project claude-demo`) before
relying on the worker.
