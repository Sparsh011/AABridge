"""Claude Code print-mode proposal worker; no model tool or shell authority.

Each turn starts one fresh `claude -p` process inside the same whole-process
macOS restrictions as Codex. All built-in tools, MCP servers and slash commands
are disabled; the only tool Claude Code exposes is its synthetic structured
output tool for the proposal schema. The first turn fixes a supervisor-chosen
session UUID and later turns resume only that exact session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import uuid
from pathlib import Path

from aabridge_adapter_codex.isolation import sandbox_profile
from aabridge_adapter_codex.proxy import ProviderProxy
from aabridge_adapter_codex.worker import INSTRUCTIONS, OUTPUT_SCHEMA, model_input, validate_proposal
from aabridge_core.config import private_directory, read_private, write_private
from aabridge_protocol.messages import BridgeError, canonical, require

PROVIDER_HOST = "api.anthropic.com"
PREFERENCE_DOMAIN = "com.anthropic.claudecode"
# Bun loads the system timezone database; it is public, non-user data.
EXTRA_ROOTS = ("/private/var/db/timezone",)
ALLOWED_TOOLS = frozenset({"StructuredOutput"})
TOKEN = re.compile(r"sk-ant-oat01-[A-Za-z0-9_-]{16,400}")
MESSAGE_LIMIT = 1_048_576
KNOWN_ERRORS = frozenset({"authentication_failed", "billing_error", "rate_limit", "invalid_request",
                          "server_error", "max_output_tokens", "overloaded"})


def validate_token(value: str) -> str:
    value = value.strip()
    require(not value.startswith("sk-ant-api"), "Anthropic API keys are not accepted; API billing fallback is disabled. Run claude setup-token and use its subscription token.", "authentication_failed")
    require(TOKEN.fullmatch(value) is not None, "Expected the long-lived subscription token printed by claude setup-token (sk-ant-oat01-…).", "authentication_failed")
    return value


def read_token(path: Path) -> str:
    try:
        data = read_private(path, 1024)
    except FileNotFoundError:
        raise BridgeError("authentication_failed", "No Claude subscription token is stored on this Mac. Run claude setup-token, then aabridge claude-token set.") from None
    try:
        return validate_token(data.decode("ascii"))
    except UnicodeDecodeError:
        raise BridgeError("authentication_failed", "Stored Claude token is malformed. Store it again with aabridge claude-token set.") from None


class ClaudeWorker:
    """Own one fresh print-mode process per turn, resuming only its saved session."""
    def __init__(self, state: Path, task: dict, project: dict, on_session, on_pid):
        self.task, self.project = task, project
        self.on_session, self.on_pid = on_session, on_pid
        self.runtime = private_directory(state / "workers" / task["id"], create=True)
        self.process = None
        self.proxy = ProviderProxy(PROVIDER_HOST)
        self.on_progress = lambda phase, message: None

    def _executable(self) -> Path:
        executable = Path(self.project["claude_executable"])
        require(executable.is_absolute() and executable.is_file() and os.access(executable, os.X_OK), "Registered Claude Code executable is unavailable. Reconnect its drive if applicable, or review the project's locally approved runtime path.", "adapter_unavailable")
        if "claude_sha256" in self.project:
            require(hashlib.sha256(executable.read_bytes()).hexdigest() == self.project["claude_sha256"],
                    "Claude Code binary changed after local registration (for example an automatic update); execution denied until the local operator revalidates it.", "adapter_changed")
        return executable

    async def _launch(self, executable: Path, arguments: list[str], token: str | None) -> None:
        home = private_directory(self.runtime / "claude-home", create=True)
        temporary = private_directory(self.runtime / "tmp", create=True)
        cwd = private_directory(self.runtime / "cwd", create=True)
        # Only this dedicated provider-runtime directory is writable. The project,
        # bridge DB/policy, keychain, unrelated files and other tasks stay inaccessible.
        proxy_port = await self.proxy.start() if token else None
        profile = self.runtime / "worker.sb"
        write_private(profile, sandbox_profile(self.runtime, executable, proxy_port=proxy_port,
                                               preference_domain=PREFERENCE_DOMAIN, extra_roots=EXTRA_ROOTS).encode())
        write_private(home / "settings.json", canonical({"disableAllHooks": True, "permissions": {"defaultMode": "dontAsk"}}).encode())
        environment = self._environment(home, temporary, proxy_port, token)
        self.process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec", "-f", str(profile), str(executable), *arguments,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=cwd, env=environment,
            start_new_session=True, limit=MESSAGE_LIMIT,
        )
        self.on_pid(self.process.pid)

    def _environment(self, home: Path, temporary: Path, proxy_port: int | None, token: str | None) -> dict:
        # Exclude API keys, provider/base-URL overrides, SSH agents and user shell settings.
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(self.runtime), "CFFIXED_USER_HOME": str(self.runtime),
                       "CLAUDE_CONFIG_DIR": str(home), "TMPDIR": str(temporary), "CLAUDE_CODE_TMPDIR": str(temporary),
                       "LANG": "en_US.UTF-8", "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1",
                       "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                       "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        if token:
            proxy_url = f"http://aabridge:{self.proxy.secret}@127.0.0.1:{proxy_port}"
            environment.update({"CLAUDE_CODE_OAUTH_TOKEN": token, "HTTPS_PROXY": proxy_url, "HTTP_PROXY": proxy_url, "ALL_PROXY": proxy_url})
        return environment

    async def start(self):
        """Local enabling preflight: binary, token and sandbox launch; no network or model turn."""
        executable = self._executable()
        read_token(Path(self.project["claude_token_file"]))
        await self._launch(executable, ["--version"], None)
        self.process.stdin.close()
        try:
            output = await asyncio.wait_for(self.process.stdout.read(4096), 15)
            await asyncio.wait_for(self.process.wait(), 5)
        except asyncio.TimeoutError:
            raise BridgeError("isolation_unavailable", "Claude Code did not start inside the proposal sandbox.") from None
        require(self.process.returncode == 0 and b"Claude Code" in output, "Claude Code could not run inside the proposal sandbox.", "isolation_unavailable")

    def _arguments(self, session: str, resume: bool) -> list[str]:
        return ["-p", "--output-format", "stream-json", "--verbose", "--input-format", "text",
                "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--permission-mode", "dontAsk",
                "--system-prompt", INSTRUCTIONS, "--json-schema", canonical(OUTPUT_SCHEMA),
                *(("--resume", session) if resume else ("--session-id", session))]

    async def _read(self) -> dict:
        try:
            line = await self.process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            raise BridgeError("worker_protocol_error", "Worker output exceeded the message limit.") from None
        require(bool(line), "Claude Code exited before completing a structured result; no result was accepted.", "worker_failed")
        try:
            value = json.loads(line)
        except ValueError:
            raise BridgeError("worker_protocol_error", "Worker emitted invalid protocol JSON.") from None
        require(isinstance(value, dict), "Worker emitted an invalid event.", "worker_protocol_error")
        return value

    async def turn(self, sources: dict[str, str]) -> dict:
        self.on_progress("connecting", "Starting the isolated Claude Code session.")
        executable = self._executable()
        token = read_token(Path(self.project["claude_token_file"]))
        resume = bool(self.task["thread_id"])
        session = self.task["thread_id"] or str(uuid.uuid4())
        await self._launch(executable, self._arguments(session, resume), token)
        self.process.stdin.write(canonical(model_input(self.task, sources)).encode())
        await self.process.stdin.drain()
        self.process.stdin.close()
        self.on_progress("resuming" if resume else "starting_session", "Resuming the saved session." if resume else "Creating a dedicated task session.")
        initialized = False
        structured = None
        while True:
            event = await self._read()
            kind = event.get("type")
            if kind not in {"system", "assistant", "user", "result", "control_request"}:
                continue  # Rate-limit, suggestion and other informational events.
            require(event.get("session_id") == session, "Cross-session worker event rejected.", "worker_protocol_error")
            if kind == "control_request":
                # Print mode has no host to answer prompts. Never self-approve.
                raise BridgeError("permission_denied", "Worker requested a permission or tool decision. It was denied.")
            if kind == "system":
                if event.get("subtype") == "init":
                    require(not initialized, "Duplicate worker session start.", "worker_protocol_error")
                    tools, servers = event.get("tools"), event.get("mcp_servers")
                    require(isinstance(tools, list) and set(tools) <= ALLOWED_TOOLS and servers == [],
                            "Effective Claude Code configuration did not retain all tool restrictions.", "permission_denied")
                    require(event.get("apiKeySource") == "none", "Claude Code is not using the subscription token; API billing fallback is disabled.", "authentication_failed")
                    initialized = True
                    self.on_session(session, session)
                    self.on_progress("waiting_for_output", "Claude Code accepted the turn; waiting for its output.")
                elif event.get("subtype") == "api_retry":
                    self.on_progress("retrying", "Claude reported a temporary connection problem and is retrying within the task deadline.")
                continue
            require(initialized, "Worker output arrived before its session start.", "worker_protocol_error")
            if kind == "assistant":
                error = event.get("error")
                if error:
                    category = error if error in KNOWN_ERRORS else "provider_error"
                    raise BridgeError("worker_failed", f"Claude stopped with {category}; no result was accepted.")
                content = (event.get("message") or {}).get("content")
                require(isinstance(content, list), "Invalid worker message.", "worker_protocol_error")
                for block in content:
                    block_type = block.get("type") if isinstance(block, dict) else None
                    if block_type == "tool_use":
                        require(block.get("name") in ALLOWED_TOOLS, "Worker attempted a tool in proposal-only mode; result rejected.", "permission_denied")
                        structured = block.get("input")
                        self.on_progress("preparing_response", "Claude is preparing its response.")
                    elif block_type in {"thinking", "redacted_thinking"}:
                        self.on_progress("working", "Claude reported work in progress; no internal reasoning is shown.")
                    else:
                        require(block_type == "text", "Unsupported worker content rejected.", "permission_denied")
                continue
            if kind == "user":
                continue  # Claude Code's own acknowledgement of the structured output tool.
            # kind == "result"
            if event.get("is_error") is not False or event.get("subtype") != "success":
                subtype = event.get("subtype")
                category = "structured_output_retries" if subtype == "error_max_structured_output_retries" else \
                    "max_turns" if subtype == "error_max_turns" else "provider_error"
                raise BridgeError("worker_failed", f"Claude stopped with {category}; no result was accepted.")
            output = event.get("structured_output", structured)
            require(isinstance(output, dict), "Claude did not complete a structured result.", "worker_failed")
            return validate_proposal(output)

    async def close(self):
        await self.proxy.close()
        if self.process is None:
            return
        if self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
        self.on_pid(None)
