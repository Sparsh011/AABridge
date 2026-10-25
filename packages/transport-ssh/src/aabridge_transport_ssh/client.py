from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

from aabridge_core.config import read_private
from aabridge_protocol.messages import BridgeError, Request, canonical, require


def ssh_arguments(remote: dict) -> list[str]:
    host, user = remote["host"], remote["user"]
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host) is not None, "Invalid configured SSH host.")
    require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", user) is not None, "Invalid configured SSH user.")
    known = Path(remote["known_hosts"])
    require(bool(read_private(known, 16384)), "Pinned host keys are missing.")
    # -o values use ssh_config tokenization even though argv bypasses a shell.
    # Quote a single file: the default macOS state path contains spaces.
    require(not any(character in str(known) for character in ('"', '\\', '\n', '\r', '%')),
            "Unsupported character in pinned host-key path.")
    identity = Path(remote["identity_file"])
    require(identity.is_absolute() and not identity.is_symlink() and identity.is_file(), "Configured bridge SSH key is unavailable.")
    info = identity.stat()
    require(info.st_uid == os.getuid() and info.st_nlink == 1 and stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077,
            "Bridge SSH private key must be privately owned and have one link.")
    require(type(remote["port"]) is int and 1 <= remote["port"] <= 65535, "Invalid configured SSH port.")
    return ["/usr/bin/ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f'UserKnownHostsFile="{known}"', "-o", "GlobalKnownHostsFile=/dev/null", "-o", "UpdateHostKeys=no",
            "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none", "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no", "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
            "-i", str(identity), "-p", str(remote["port"]), "-l", user]


def call(remote: dict, request: Request) -> dict:
    command = [*ssh_arguments(remote), "--", remote["host"], "aabridge-rpc-v1"]
    try:
        result = subprocess.run(command, input=(canonical(request.to_dict()) + "\n").encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise BridgeError("remote_unavailable", "SSH transport failed or timed out; task lifetime is independent of this connection.") from None
    if result.returncode:
        mismatch = b"HOST IDENTIFICATION HAS CHANGED" in result.stderr or b"Host key verification failed" in result.stderr
        raise BridgeError("host_identity_mismatch" if mismatch else "remote_unavailable",
                          "SSH host identity did not match the pinned key." if mismatch else "SSH authentication, reachability, or restricted entry point failed; no raw SSH output returned.")
    require(len(result.stdout) <= 262144, "Remote response exceeds limit.")
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise BridgeError("remote_protocol_error", "Remote endpoint returned malformed JSON.") from None
    if "error" in response:
        raise BridgeError(response["error"]["code"], response["error"]["message"])
    return response["result"]
