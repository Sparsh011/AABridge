# Shared packages

- `preflight/`: local prerequisite diagnostics and shared per-agent runtime location discovery.
- `protocol/`: strict request validation and packaged v1 request/task/event/result schemas.
- `core/`: local configuration, authentication/authorization and SQLite state.
- `workspace/`: isolated Git clones, proposed edits and restricted AST verification.
- `transport-local/`: authenticated private Unix socket client.
- `transport-ssh/`: non-interactive pinned-key SSH client.
- `adapter-codex/`: managed app-server sessions, proposal isolation and provider relay.

Agent-specific behavior belongs in its own adapter package. Claude and Gemini
worker adapters have not been implemented yet. Application entry points belong
under `apps/`, not beside these libraries or at the repository root.
