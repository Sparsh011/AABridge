# MacBook–Mac mini setup

The first physical connection and Codex question have passed; full result and
revision testing awaits the receiver cleanup update described in [verification](verification.md). No password,
private key, provider token or API key should be pasted into chat.

## First step if you do not know the mini's address

On the **Mac mini**, open System Settings → General → Sharing → Remote Login
(the information button). Enable it for the intended account using “Only these
users.” The panel shows a line like `ssh username@hostname`; copy that line for
setup. Full disk access is not needed for this demo. These steps and the location
of the SSH line follow [Apple's Remote Login guide](https://support.apple.com/en-ie/guide/mac-help/mchlp1066/27/mac/27).

Keep both Macs on the same reachable LAN or an already configured VPN. This
project does not open router ports, discover machines or configure a VPN. All
remaining commands use placeholders determined on your devices, not guessed
usernames or addresses.

## Prepare the mini locally

The [native receiver setup app](desktop.md) provides file pickers and setup
progress without opening Terminal. Use the current desktop preview ZIP for
assisted setup. The following command bundle and manual steps remain alternatives.

For assisted setup, a coordinator can prepare a ZIP containing the built wheel,
installer and dedicated **public** key:

```sh
.venv/bin/python scripts/make_pairing_bundle.py \
  --public-key /absolute/path/to/dedicated-key.pub --peer macbook \
  --output /absolute/path/to/AABridge-Mini-Setup.zip
```

AirDrop that ZIP to the mini through the trusted setup channel, extract it, and
double-click `Setup.command` under the intended Remote Login account. Inspect
`READ-ME.txt` and the scripts first if desired. The installer creates a private
environment, installs the included wheel offline, registers a disposable demo,
runs Codex authentication/isolation preflight, grants the bundled public key
only the demo project, appends its restricted SSH entry while preserving existing
key lines, and starts the supervisor. It prints `AABRIDGE MINI READY` on success.
Setup does not start model inference. A missing/failed Codex preflight prevents
installation of the SSH entry; sign in locally and rerun if required.

The wrapper uses an available Python 3.11+ or installs Python 3.11 through an
already installed Homebrew. It does not bootstrap Homebrew, run sudo, transfer
provider credentials or include any private key. Private state/environment are
under `~/Library/Application Support/AABridge` and `AABridgeInstall`. Checksum
checks detect corrupted payloads; the trusted transfer/review supplies trust,
not a signature. Existing peer-key conflicts, revocation and unexpected project
registrations stop setup instead of being overwritten. Existing `.ssh` must be
private; unsafe links/ownership or writable key files are rejected.

The following manual instructions are an alternative to the setup bundle.

The installer supports an existing ChatGPT app on an SSD through the local
`runtime-paths.json` in the pairing folder. Claude Code has its own independent
selection and local CLI discovery. See [runtime locations](runtime-locations.md).
Keep a selected SSD attached; unavailable selected locations do not fall back
to another installation. State and workspaces stay in the Mac's home directory.

The 25 October installer update distinguishes ChatGPT's native Codex executable
from its bundled launcher. If the earlier bundle said to install Codex despite
ChatGPT already being installed, use the updated bundle. It prints the selected
executable path and, if needed, a local file-based ChatGPT login command. After
that login, rerun `Setup.command`. This fixes a reproduced local discovery bug;
the mini's actual preflight still has to pass.

Install Python 3.11+ and Git, and locate a compatible Codex runtime. That runtime
may already be bundled inside the mini's ChatGPT app; a separate Codex app or
standalone CLI installation is not inherently required. Use the mini's own
existing ChatGPT account. Confirm `codex login status` locally when the CLI is
on PATH, or use the verified bundled executable's absolute path;
never copy its auth.json to the other computer. The tested Codex version is
recorded in [verification](verification.md); a different version needs its own
preflight. No Claude access is needed for this test.

Transfer the built AABridge wheel through a trusted channel such as AirDrop.
In Terminal on the mini, create an environment in a stable local installation
folder (replace the wheel filename with the actual transferred artifact):

```sh
python3.11 -m venv "$HOME/aabridge-env"
"$HOME/aabridge-env/bin/python" -m pip install --no-index --no-deps /absolute/path/to/aabridge-0.2.0.dev1-py3-none-any.whl
"$HOME/aabridge-env/bin/aabridge" init
"$HOME/aabridge-env/bin/aabridge" demo-init --directory "$HOME/aabridge-demo" --project demo
"$HOME/aabridge-env/bin/aabridge" project enable --id demo
"$HOME/aabridge-env/bin/aabridge" start
```

Enabling runs non-model isolation/authentication checks. Submitting tasks uses
Codex subscription usage. The demo source baseline is already present on the
mini; discovery returns its exact commit to the MacBook. For other projects,
approved source commits must be transferred and registered locally first;
automatic coordinator-to-worker source publishing is not implemented.

## Dedicated bridge key and explicit grant

On the **MacBook**, generate a new dedicated key (do not overwrite an existing
one). `BatchMode` and disabled agent forwarding require it to work unattended;
this dedicated key has an empty passphrase and must remain mode 0600 locally.

```sh
ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/aabridge_mini"
```

Transfer **only** `aabridge_mini.pub` to the mini through the trusted setup
channel. On the **mini**, grant that public key only the demo project:

```sh
"$HOME/aabridge-env/bin/aabridge" peer grant --id macbook \
  --project demo --public-key /absolute/path/to/aabridge_mini.pub
```

The command returns the path to an `authorized-key-macbook.txt` file. Inspect it
locally and append that complete one-line entry to the intended account's
`~/.ssh/authorized_keys`, preserving existing entries. Ensure `.ssh` is owned by
that account with mode 0700 and `authorized_keys` mode 0600. Do not install the
bare public key: the generated entry includes `restrict` and the fixed gateway
command. The installed environment/state paths must remain available for SSH.

The key can use bridge RPC and authorized Git result reads, without a shell,
TTY or forwarding. It still authenticates into a trusted local account: this is
not a separate OS security boundary from that account's other processes.

## Verify the mini's host identity

On the **mini**, display its public host-key fingerprint:

```sh
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Bring that `SHA256:...` fingerprint to the MacBook through the trusted setup
channel. It is public. On the **MacBook**, collect a host-key candidate into a
new file (replace `MINI_HOST` with the panel's hostname/address):

```sh
ssh-keyscan -t ed25519 MINI_HOST > "$HOME/aabridge-mini-host-candidate"
```

`ssh-keyscan` alone does not authenticate anything. Enrollment below accepts it
only if it matches the fingerprint read on the mini. Replace `MINI_USER` and
`SHA256:TRUSTED_FINGERPRINT` with the mini's actual values:

```sh
.venv/bin/aabridge remote-add --name mini --host MINI_HOST --user MINI_USER \
  --identity-file "$HOME/.ssh/aabridge_mini" \
  --known-hosts "$HOME/aabridge-mini-host-candidate" \
  --fingerprint SHA256:TRUSTED_FINGERPRINT
.venv/bin/aabridge project list --remote mini
```

Initialize MacBook state first if needed (`aabridge init`). A matching enrollment
copies the pinned key into private bridge state; it never silently replaces a
registered remote. A mismatch stops setup. SSH port defaults to 22; use an
explicit `--port` and matching `[host]:port` candidate for a different port.

## Run the remote round trip

For this SSH setup, keep Remote Login enabled on the receiving mini while remote
connections are wanted. Screen Sharing, Remote Management and File Sharing are
not used. Outbound-only use does not require Remote Login on the MacBook.
See [the implementation overview](implementation-overview.md) for the setting
matrix, design rationale and ChatGPT-bundled Codex clarification.

Run the README task commands on the MacBook, adding `--remote mini` to each
`task submit`, `wait`, `answer`, `revise`, `events` and `status` operation. Use the
returned remote task/question IDs. The worker and durable state live on the
mini; disconnecting the MacBook does not cancel the task. An active MCP
coordinator uses `remote: "mini"` on each bridge tool instead.

Fetch the completed result for local review:

```sh
.venv/bin/aabridge task fetch --remote mini --id TASK_ID
```

This authenticates a Git fetch into private local `reviews/TASK_ID`, verifies the
exact result SHA and returns a bounded diff. It does not modify any active
checkout or require GitHub. Source URLs/commands received from the worker are
not executed. Record the question, same session ID before/after the answer,
first/revised commit IDs, local fetched diff and verifier evidence. Only then
mark physical two-device execution passed.

To revoke on the **mini**:

```sh
"$HOME/aabridge-env/bin/aabridge" peer revoke --id macbook
```

Revocation blocks task/Git gateway access even while the public SSH entry
remains present. Remove that entry locally when appropriate. `aabridge stop`
on the mini stops its supervisor. Reverse delegation uses separate enrollment
in the other direction and a locally registered receiving project.
