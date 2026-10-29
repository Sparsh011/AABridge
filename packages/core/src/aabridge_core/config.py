from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

from aabridge_protocol.messages import BridgeError, canonical, identifier, require


def private_directory(path: Path, *, create: bool = False) -> Path:
    path = path.expanduser()
    require(path.is_absolute() and ".." not in path.parts, "State path must be absolute without traversal.")
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            require(create, "State is not initialized. Run aabridge init.", "not_initialized")
            item.mkdir(mode=0o700)
            info = item.lstat()
        require(stat.S_ISDIR(info.st_mode), "State path must contain only real directories.", "unsafe_state")
        require(info.st_uid in (0, os.getuid()), "State ancestor has an unexpected owner.", "unsafe_state")
        writable = stat.S_IMODE(info.st_mode) & 0o022
        require(not writable or bool(info.st_mode & stat.S_ISVTX), "State ancestor is writable by other users.", "unsafe_state")
    info = path.lstat()
    require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077, "State directory must be owned by you and private (0700).", "unsafe_state")
    return path


def read_private(path: Path, maximum: int = 262144) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not stat.S_IMODE(info.st_mode) & 0o077, "Unsafe private state file.", "unsafe_state")
        data = stream.read(maximum + 1)
        require(len(data) <= maximum, "State file is too large.", "unsafe_state")
        return data


def write_private(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    require(not path.is_symlink(), "Refusing a symlinked state file.", "unsafe_state")
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not stat.S_IMODE(info.st_mode) & 0o077, "Unsafe private state file.", "unsafe_state")
        os.ftruncate(stream.fileno(), 0)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def initialize(state: Path) -> dict:
    state = private_directory(state, create=True)
    require(not (state / "config.json").exists(), "State already initialized; nothing was changed.", "already_initialized")
    token = secrets.token_urlsafe(32)
    config = {"version": 1, "node_id": "node-" + secrets.token_hex(8), "projects": {}, "remotes": {},
              "peers": {"local": {"digest": token_digest(token), "projects": ["*"], "enabled": True}}}
    write_private(state / "local.token", token.encode(), exclusive=True)
    save_config(state, config, exclusive=True)
    for name in ("workspaces", "workers"):
        private_directory(state / name, create=True)
    return {"node_id": config["node_id"], "state": str(state)}


def load_config(state: Path) -> dict:
    private_directory(state)
    try:
        config = json.loads(read_private(state / "config.json"))
    except (FileNotFoundError, ValueError):
        raise BridgeError("not_initialized", "Missing or invalid local configuration.") from None
    require(config.get("version") == 1, "Unsupported configuration version.")
    return config


def save_config(state: Path, config: dict, *, exclusive: bool = False) -> None:
    # A local configuration operation, never an RPC. The daemon reloads policy
    # on every request. Replace atomically so readers never see partial policy.
    if exclusive:
        write_private(state / "config.json", canonical(config).encode(), exclusive=True)
        return
    staging = state / ("config-" + secrets.token_hex(8) + ".tmp")
    write_private(staging, canonical(config).encode(), exclusive=True)
    os.replace(staging, state / "config.json")
    descriptor = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def authenticate(config: dict, token: object) -> str:
    require(isinstance(token, str) and len(token) <= 128, "Invalid client credential.", "unauthorized")
    digest = token_digest(token)
    for identity, peer in config["peers"].items():
        if peer["enabled"] and isinstance(peer.get('digest'), str) and secrets.compare_digest(peer["digest"], digest):
            return identifier(identity)
    raise BridgeError("unauthorized", "Unknown or revoked client credential.")


def authorize(config: dict, identity: str, project: str) -> dict:
    peer = config["peers"].get(identity, {})
    require(peer.get("enabled", False), "Peer is not enrolled.", "unauthorized")
    require(project in peer.get("projects", []) or "*" in peer.get("projects", []), "Project access denied.", "permission_denied")
    require(project in config["projects"], "Project is not registered locally.", "permission_denied")
    return config["projects"][project]
