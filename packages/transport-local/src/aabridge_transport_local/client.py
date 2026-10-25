from __future__ import annotations

import json
import socket
import stat
from pathlib import Path

from aabridge_core.config import private_directory, read_private
from aabridge_protocol.messages import BridgeError, MAX_MESSAGE, Request, canonical, identifier, require


def call(state: Path, request: Request, *, peer: str = "local") -> dict:
    state = private_directory(state)
    identifier(peer)
    token = read_private(state / ("local.token" if peer == "local" else f"peer-{peer}.token"), 128).decode()
    endpoint = state / "bridge.sock"
    try:
        info = endpoint.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == state.stat().st_uid and not info.st_mode & 0o077,
                "Unsafe IPC socket ownership/type/permissions.", "unsafe_state")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(endpoint))
            payload = (canonical({"token": token, "request": request.to_dict()}) + "\n").encode()
            require(len(payload) <= MAX_MESSAGE, "Request exceeds IPC message limit.")
            connection.sendall(payload)
            with connection.makefile("rb") as stream:
                data = stream.readline(262145)
                require(len(data) <= 262144 and data.endswith(b"\n"), "Invalid or oversized IPC response.")
        result = json.loads(data)
        if "error" in result:
            raise BridgeError(result["error"]["code"], result["error"]["message"])
        return result["result"]
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
        raise BridgeError("daemon_unavailable", "Bridge daemon is unavailable. Run aabridge start with the same state directory.") from None
