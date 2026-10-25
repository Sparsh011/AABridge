# Applications

Runnable entry points are separated from reusable libraries:

- `cli/`: installation, local setup, peer enrollment and bounded task commands.
- `desktop/`: native macOS task chat and controls, authenticated local/SSH backend,
  receiver setup, file pickers, diagnostics and readiness.
- `daemon/`: private IPC service, durable lifecycle and managed worker supervision.
- `mcp-server/`: stdio tools used by active coding-agent coordinators.
- `installer/`: reviewable macOS worker setup, delivered with an offline wheel
  and the coordinator's public SSH key.

The Python distribution packages the CLI, daemon and MCP server with shared
libraries. The desktop build packages a native app and an offline pairing payload.
