# Agent compatibility

Host location, agent runtime and underlying model are separate choices. The
bridge task/transport protocol does not assume Claude is the remote provider.
Only locally installed and authorized adapters may run; a runtime name is not
an executable path or permission to download one.

| Pairing | Current implementation/evidence |
| --- | --- |
| Active Codex coordinator → managed Codex | Working proposal-v1 adapter; real local question/answer/exact resume/result/revision passed through CLI operations |
| MCP coordinator → managed Codex | stdio handshake, complete tool schemas and protected IPC tested; installed live MCP coordinator workflow still pending |
| Codex MacBook → Codex Mac mini | Restricted SSH path implemented; real localhost transport passed, physical mini pending setup |
| Claude active coordinator → Codex worker | MCP setup documented; live coordinator not tested |
| Any coordinator → managed Claude Code worker | Implemented ([guide](claude-worker.md)); deterministic tests and a real-binary sandbox/question/exact-resume check against a local fake provider pass; live subscription task pending |
| Managed Gemini CLI worker | Not implemented or tested; must validate headless sessions, authentication, permissions and isolation independently |

The adapter uses Codex app-server JSON-RPC and the user's local ChatGPT account.
The tested installed build is 0.163.0-alpha.11.2. It validates effective feature
restrictions, declines any permission request, records thread/session IDs and
resumes only that exact thread. Provider retry notifications are bounded by the
same task deadline. There is no API-key fallback or explicit costly model choice.
A changed installed binary hash requires local revalidation.

Questions are completed structured turns: the supervisor persists them and
closes the worker process, then starts a fresh process resuming the saved thread
after an authorized answer. This is independent of provider-native interactive
question tooling. MCP is for active coordinators; managed workers have all tools,
including MCP and recursive delegation, disabled.

A successful Codex test cannot certify Claude/Gemini authentication, exact resume,
permission behavior or cancellation. Each new adapter needs deterministic
conformance tests and real bounded evidence. Test-only deterministic workers are
internal fixtures for reproducible failures; users are not asked to deploy fake
agents, and they do not substitute for live or physical remote tests.

References checked during implementation:
[Codex app-server](https://learn.chatgpt.com/docs/app-server),
[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp),
[Claude MCP](https://code.claude.com/docs/en/mcp),
[Claude programmatic execution](https://code.claude.com/docs/en/headless),
[Gemini headless interface](https://geminicli.com/docs/cli/headless/).
Installed protocol schemas take precedence over examples for the tested build;
other installations need explicit preflight rather than assumed compatibility.
