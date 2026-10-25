"""Native coding CLIs. Enabled only by a local repository-v1 project choice.

The CLI can edit its isolated clone and run project tools. This is deliberately
not the proposal sandbox: normal user login and CLI permission settings apply.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import uuid
from pathlib import Path

from aabridge_core.config import read_private
from aabridge_protocol.messages import BridgeError, canonical, require


class NativeWorker:
    def __init__(self, state, task, project, on_session, on_pid):
        self.state, self.task, self.project = state, task, project
        self.on_session, self.on_pid = on_session, on_pid
        self.on_progress = lambda *args: None
        self.process = None

    def executable(self):
        agent = self.project['adapter']
        path = Path(self.project[agent + '_executable'])
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                'The selected agent is unavailable. Choose its location in Settings.', 'adapter_unavailable')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == self.project[agent + '_sha256'],
                'The agent was updated. Save this project again to revalidate it.', 'adapter_changed')
        return str(path)

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(self.executable(), '--version',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        self.on_pid(self.process.pid)
        output, _ = await self.process.communicate()
        require(self.process.returncode == 0 and output, 'The agent could not start. Sign in to its CLI on this Mac.', 'adapter_unavailable')

    def arguments(self):
        agent = self.project['adapter']
        readonly = self.task.get('mode', 'work') != 'work'
        session = self.task.get('thread_id')
        if agent == 'codex':
            # Global config works with both exec and exec resume. Never disable
            # the CLI sandbox or silently approve broader permissions.
            common = ['-c', 'sandbox_mode="' + ('read-only' if readonly else 'workspace-write') + '"',
                      '-c', 'approval_policy="never"']
            if session:
                return common + ['exec', 'resume', '--json', session, '-']
            return common + ['exec', '--json', '-']
        tools = 'Read,Glob,Grep' if readonly else 'Read,Glob,Grep,Edit,Write,Bash'
        return ['-p', '--output-format', 'stream-json', '--verbose', '--permission-mode', 'acceptEdits',
                '--tools', tools, '--allowedTools', tools, '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                *((['--resume', session]) if session else ['--session-id', str(uuid.uuid4())])]

    async def turn(self, sources):
        workspace = self.state / 'workspaces' / self.task['id']
        mode = self.task.get('mode', 'work')
        prompt = ('You are working in AABridge. The human is the admin. Follow the assigned team role. '
                  'Work only in this checkout and keep its branch. Do not push, merge, deploy, or change remotes. '
                  'Report what changed, checks actually run, and remaining issues. '
                  + ('Inspect and respond only; do not edit files or create commits. ' if mode != 'work' else '')
                  + '\nTask:\n' + self.task['objective'] + '\nContext:\n' + canonical(self.task.get('context', []))
                  + '\nFollow-up:\n' + canonical(self.task.get('pending')))
        environment = dict(os.environ)
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
                    'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL'):
            environment.pop(key, None)
        if self.project['adapter'] == 'claude' and (self.state / 'claude-oauth-token').exists():
            environment['CLAUDE_CODE_OAUTH_TOKEN'] = read_private(self.state / 'claude-oauth-token', 1024).decode().strip()
        environment['PATH'] = str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:' + environment.get('PATH', '/usr/bin:/bin')
        self.on_progress('working', 'The coding agent is working in its task branch.')
        self.process = await asyncio.create_subprocess_exec(self.executable(), *self.arguments(), cwd=workspace,
                env=environment, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True, limit=2_000_000)
        self.on_pid(self.process.pid)
        self.process.stdin.write(prompt.encode())
        await self.process.stdin.drain()
        self.process.stdin.close()
        summary = ''
        succeeded = False
        while line := await self.process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                raise BridgeError('worker_protocol_error', 'The coding agent returned invalid output.') from None
            if self.project['adapter'] == 'codex':
                if event.get('type') == 'thread.started':
                    session = event['thread_id']
                    self.on_session(session, session)
                if event.get('type') == 'item.completed':
                    item = event.get('item', {})
                    if item.get('type') == 'agent_message':
                        summary = item.get('text', '')
                if event.get('type') == 'turn.completed':
                    succeeded = True
            else:
                if event.get('type') == 'system' and event.get('subtype') == 'init':
                    session = event['session_id']
                    self.on_session(session, session)
                if event.get('type') == 'result':
                    succeeded = event.get('is_error') is False and event.get('subtype') == 'success'
                    summary = event.get('result', '')
        await self.process.wait()
        require(self.process.returncode == 0 and succeeded and isinstance(summary, str) and summary.strip(),
                'The agent stopped before completing. Check its login and CLI permissions; partial files are preserved.', 'worker_failed')
        summary = summary[:60000]
        while len(canonical(summary).encode()) > 80000:
            summary = summary[:len(summary) * 3 // 4]
        return {'kind': 'result', 'summary': summary, 'edits': [], 'issues': [], 'question': ''}

    async def close(self):
        if self.process and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.process.wait()
        self.on_pid(None)
