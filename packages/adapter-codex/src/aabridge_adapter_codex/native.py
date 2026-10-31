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

from aabridge_core.config import private_directory, read_private
from aabridge_protocol.messages import BridgeError, canonical, require


class NativeWorker:
    def __init__(self, state, task, project, on_session, on_pid):
        self.state, self.task, self.project = state, task, project
        self.on_session, self.on_pid = on_session, on_pid
        self.on_progress = lambda *args: None
        self.process = None
        self.on_activity = lambda event: None

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

    def review_runtime(self):
        return private_directory(self.state / 'review-runtime' / self.task['id'], create=True)

    def arguments(self):
        agent = self.project['adapter']
        review = self.task.get('mode') == 'review' and bool(self.task.get('review_snapshot'))
        readonly = self.task.get('mode', 'work') != 'work' and not review
        session = self.task.get('thread_id')
        settings = self.task.get('model_settings', {})
        selected = ['--model', settings['model']] if settings.get('model') else []
        from aabridge_workspace import linked
        links = linked.paths(self.state, self.task, self.project) if self.project.get('links') else []
        if agent == 'codex':
            # Global config works with both exec and exec resume. Never disable
            # the CLI sandbox or silently approve broader permissions.
            common = ['-c', 'sandbox_mode="' + ('read-only' if readonly else 'workspace-write') + '"',
                      '-c', 'approval_policy="never"']
            if settings.get('effort'):
                common += ['-c', 'model_reasoning_effort=' + json.dumps(settings['effort'])]
            writable = [str(path) for link, path in links if link['access'] == 'edit'] if not review else [str(self.review_runtime())]
            if writable and not readonly:
                # Work gets edit links; review gets only its private scratch root.
                common += ['-c', 'sandbox_workspace_write.writable_roots=' + json.dumps(writable)]
            if session:
                return common + ['exec', 'resume', '--json', *selected, session, '-']
            return common + ['exec', '--json', *selected, '-']
        tools = 'Read,Glob,Grep' if readonly else 'Read,Glob,Grep,Bash' if review else 'Read,Glob,Grep,Edit,Write,Bash'
        if settings.get('effort'):
            selected += ['--effort', settings['effort']]
        selected += [argument for _, path in links for argument in ('--add-dir', str(path))]
        return ['-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages', *selected, '--permission-mode', 'acceptEdits',
                '--tools', tools, '--allowedTools', tools, '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                *((['--resume', session]) if session else ['--session-id', str(uuid.uuid4())])]

    async def turn(self, sources):
        workspace = self.state / 'workspaces' / self.task['id']
        mode = self.task.get('mode', 'work')
        from aabridge_workspace import linked
        links = linked.describe(self.state, self.task, self.project)
        prompt = ('You are working in AABridge. The human is the admin. Follow the assigned team role. '
                  'Work only in this checkout' + (' and the linked projects listed below' if links else '') + ', and keep their branch. Do not push, merge pull requests, deploy, or change remotes. '
                  'AABridge captures your changes and handles branch placement, authenticated pushes and PR creation when selected in the room. '
                  'Do not ask the admin to repeat delivery permission or treat your checkout having no remote as a blocker; report any bridge delivery error separately. '
                  'The leader plans and reviews; the follower implements in this workflow. '
                  'Report what changed, checks actually run, and remaining issues. '
                  'Give the human short progress updates as you work: what you are doing, files you are changing, and check results. Do not disclose private internal reasoning. '
                  + ('Inspect and respond only; do not edit files or create commits. ' if mode != 'work' else
                     'When the task requires target-branch reconciliation, you may fetch its supplied URL and merge the target into this isolated task branch. '
                     'Resolve conflicts, preserve both histories, and rerun checks. Never merge into a protected or source-project branch. ')
                  + ('The bridge checked out the exact implementation commit ' + self.task['review_snapshot']['commit'] +
                     '. Inspect these files and the cumulative diff from ' + self.task['base_commit'] +
                     '; do not confuse the original baseline with the reviewed head. ' if self.task.get('review_snapshot') else '')
                  + ('\n' + links if links else '')
                  + '\nTask:\n' + self.task['objective'] + '\nContext:\n' + canonical(self.task.get('context', []))
                  + '\nFollow-up:\n' + canonical(self.task.get('pending')))
        environment = dict(os.environ)
        if mode == 'review' and self.task.get('review_snapshot'):
            runtime = self.review_runtime()
            for variable, directory in (('TMPDIR', 'tmp'), ('XDG_CACHE_HOME', 'cache'), ('GOCACHE', 'go-build')):
                environment[variable] = str(private_directory(runtime / directory, create=True))
            prompt += ('\nReview validation is authorized in this isolated snapshot. Run appropriate checks when possible. '
                       'Use ' + environment['TMPDIR'] + ' for temporary/build outputs. '
                       'Keep tracked source, the index, branch, and HEAD unchanged; AABridge rejects a modified review checkout. '
                       'Report actual test failures or unavailable dependencies precisely; do not ask for source-edit permission to review.')
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
        from .activity import ActivityStream
        activity = ActivityStream(self.on_activity)
        settings = self.task.get('model_settings', {})
        activity.emit('settings', 'status', 'Selected model: ' + settings.get('model', 'agent default') + ' · effort: ' + settings.get('effort', 'agent default'), force=True)
        while line := await self.process.stdout.readline():
            try:
                event = json.loads(line)
            except ValueError:
                raise BridgeError('worker_protocol_error', 'The coding agent returned invalid output.') from None
            if self.project['adapter'] == 'codex':
                activity.codex(event)
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
                activity.claude(event)
                if event.get('type') == 'system' and event.get('subtype') == 'init':
                    session = event['session_id']
                    self.on_session(session, session)
                if event.get('type') == 'result':
                    succeeded = event.get('is_error') is False and event.get('subtype') == 'success'
                    summary = event.get('result', '')
        await self.process.wait()
        require(self.process.returncode == 0 and succeeded and isinstance(summary, str) and summary.strip(),
                'The agent stopped before completing. Check its login, selected model/effort, and CLI permissions; partial files are preserved.', 'worker_failed')
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
