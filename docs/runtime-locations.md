# Agent locations: internal storage and external SSDs

Each receiving host keeps a separate local selection for Codex and Claude Code.
An application on an external SSD and another agent on internal storage can be
discovered independently. AABridge does not require copying either installation
into its repository, installation environment or working directory.

This feature handles discovery and local registration. Codex has a working
managed worker adapter. Claude Code can be located and inventoried, but its
managed worker adapter is still unimplemented. Discovery does not establish
login, subscription access or safe unattended execution.

## Local commands

After `aabridge init`, inspect locations without executing an agent or logging in:

```sh
aabridge runtime list
aabridge runtime set --agent codex --path '/Volumes/Example SSD/Downloads/ChatGPT.app'
aabridge runtime set --agent claude --path "$HOME/.local/bin/claude"
```

`runtime set` is a local operator action. It remembers the selection even if the
drive is offline and reports `status: unavailable`. It does not enable execution.
Use `--auto` instead of `--path` to explicitly restore automatic discovery for
that agent. These operations are not exposed to peers or MCP coordinators.

An explicit path takes precedence over PATH and automatic locations. If a
selected SSD is missing, the bridge reports the selected path and a reconnect
hint, without silently switching to an internal copy. A different agent remains
independent. Runtime reports distinguish finding a file from adapter availability
and leave authentication marked `not_checked`.

Automatic Codex discovery uses PATH and the standard system/user Applications
folders. It selects the native executable inside ChatGPT/Codex rather than
treating its launcher as another installation. An explicit Codex location can
be an app bundle, the app path with Finder's hidden `.app` suffix omitted, or
an executable. Paths with spaces are supported through structured file/argv
operations. No entire-disk or all-volume search is performed.

Automatic Claude CLI discovery uses PATH, `~/.local/bin/claude`,
`/opt/homebrew/bin/claude` and `/usr/local/bin/claude`. Native-install symlinks are
resolved to their executable. Set another absolute executable path explicitly
for another installation. Distinct ambiguous installations require a selection;
no provider login or model turn is part of discovery.

`doctor` also respects saved paths for version/help checks. Those checks execute
trusted local CLI binaries but never request model inference or a provider login.
Use `runtime list` for metadata-only inventory.

## Pairing installer

Pairing ZIPs contain an editable `runtime-paths.json`, for example:

```json
{
  "version": 1,
  "codex": "/Volumes/Example SSD/Downloads/ChatGPT.app",
  "claude": null
}
```

Run `Setup.command` on the receiving Mac. These are paths on that Mac, not on the
coordinator. Omit a field to preserve a saved selection; use `null` for explicit
automatic discovery. The installer prints each agent's discovery outcome, saves
the selections locally, and requires Codex preflight before installing the demo
SSH entry. An unavailable optional Claude installation does not block Codex setup.

The bundle builder accepts `--codex-path` and `--claude-path`; it validates local
path syntax without requiring the worker's SSD to be attached to the coordinator.
The bundle contains a public key and path preferences, never provider credentials
or the coordinator's private key.

## Persistence and protection

The selected application may remain on the SSD. The default bridge database,
tokens, environment, worker HOME/CODEX_HOME, logs and demo clones remain on the
Mac's internal home directory. Provider credentials are resolved through the
worker account's existing local configuration, not the application's SSD folder.

A newly registered project pins its resolved Codex executable and SHA-256 hash.
Changing a general runtime selection does not silently rewrite existing project
approvals. If a project's approved binary changes, local revalidation is needed.
The installer refuses an existing demo pinned to a different executable; register
a new reviewed project rather than silently repurposing its approval.

Keep the selected SSD mounted at its selected location while starting/using
that runtime. A missing/non-executable registered binary prevents worker start.
Disconnect-related runtime errors reject incomplete attempts; partial state is
retained. The macOS read grant covers the selected app bundle, not its whole SSD.
An OS permission or execution restriction still has to pass the real preflight;
this feature does not bypass macOS security controls.

Each adapter continues to need its own execution, authentication and session
tests. Finding a local Claude CLI does not make it a working managed Claude
worker and does not initiate Claude usage.
