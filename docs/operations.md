# Operations and troubleshooting

`aabridge doctor` only checks prerequisites. Its legacy JSON field
`worker_execution_enabled: false` means that diagnostic never enables execution;
it does not inspect configured project gates. `project list` reports actual
local/remote gates. `project enable --id ID` runs isolation and account/config
preflight, without a model turn. `project disable --id ID` stops scheduling and
cancels active work for that project.

## Status, errors and recovery

- `daemon_unavailable`: run `aabridge start`. If startup fails, use `aabridge serve`
  in a terminal for a bounded error. State/socket must be private; use a shorter
  absolute state path if macOS's Unix socket name limit is exceeded.
- `execution_disabled` / `isolation_unavailable`: execution stays off until local
  preflight succeeds. Do not widen the profile or enable a bypass to force a run.
- `authentication_failed`: sign in to Codex locally under the worker account and
  rerun preflight. Keychain-only/API authentication is not supported in this slice.
  Do not share auth files or tokens.
- `adapter_changed`: registered binary hash no longer matches. Disable execution,
  validate the new installed protocol and restrictions, and re-register in fresh
  state with reviewed policy; there is no automatic binary trust update command.
- `host_identity_mismatch`: compare with the host itself through a trusted channel.
  Do not disable strict checks or replace pinned keys automatically.
- `direct_unavailable`: check that AABridge is running on the other Mac and its
  saved LAN/VPN address and app port are reachable. Remote Login is not used.
  Update both receivers and exchange new version-2 cards when upgrading SSH pairs.
- `remote_unavailable` (legacy SSH): check the panel's address/account, LAN/VPN, Remote Login,
  dedicated key and forced-command executable. No interactive SSH password
  fallback or forwarding is enabled. Reconnect and query the saved task ID.
- `replay_conflict`: a mutation key was reused with changed content. Exact retries
  retain the same key/body; a distinct mutation needs a distinct key.
- `stale_question` / `stale_revision`: read the current status and answer that exact
  question/revision. Never select the most recent conversation as a substitute.
- Provider errors fail without accepting a result. Transient retry notifications
  keep the same task deadline. Read status/events for safe error codes; raw provider
  logs are not forwarded. Limits are not a guaranteed financial cap.

Cancel with `task cancel --id ID` (and `--remote NAME` for a remote task). The
supervisor stops its owned process group with TERM then bounded KILL fallback
and retains partial state/workspace. Closing the calling connection does not
cancel. Stopping the supervisor interrupts active tasks; queued/question tasks
remain durable. Restart does not automatically replay uncertain active work.

After interruption, disable the project and inspect its recorded worker process
locally. Only after verifying it no longer exists:

```sh
aabridge project disable --id PROJECT_ID
aabridge project reconcile --id PROJECT_ID
aabridge project enable --id PROJECT_ID
```

Reconcile refuses while a recorded PID exists; PID reuse can conservatively block
it. It never kills an arbitrary recorded PID. The operator must investigate an
ambiguous or surviving process before re-enabling. Interrupted artifacts are
retained and are not automatically resumed/replaced.

## Verification profile and storage

`examples/integer-function.json` registers one existing Python file containing
one simple integer function with a return expression and optional docstring.
Only bounded integer constants, parameters, signs, +, -, *, // and % are
interpreted. Imports, calls, annotations, decorators, loops, module statements,
additional functions and file deletion are rejected. This is an independent
profile check, not execution of pytest or the application's test suite.

Task state, provider transcripts, private credentials/links, clones and reviews
are under the private state directory. Do not upload that directory as a bug
report. Share bounded status/error/evidence after reviewing for sensitive source.
There is no automatic retention deletion; monitor disk space. Never copy active
SQLite files for backup without stopping the supervisor and other clients first.

## Removal

Stop the supervisor on each host, revoke peers and remove only their reviewed
forced-command entries from authorized_keys. Remove coordinator MCP registration
with the host's MCP removal command. Uninstall the package from its environment
with `python -m pip uninstall aabridge`; retain or manually archive state/results
as desired. No login agent, system SSH edit, automatic cleanup or auto-publishing
service is installed. Python 3.11 can remain separately installed.
