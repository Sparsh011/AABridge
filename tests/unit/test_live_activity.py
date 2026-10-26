from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aabridge_adapter_codex.activity import ActivityStream
from aabridge_adapter_codex.native import NativeWorker
from aabridge_cli.actions import request
from aabridge_core.config import initialize, load_config, save_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError, MUTATIONS, model_settings


class PublicActivityTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.stream = ActivityStream(self.events.append)

    def test_codex_tools_files_and_public_messages_without_private_payloads(self):
        for event in [
            {'type':'item.started','item':{'id':'command','type':'command_execution','command':'echo never-show-this-command-secret'}},
            {'type':'item.completed','item':{'id':'command','type':'command_execution','exit_code':0,'aggregated_output':'never-show-this-output'}},
            {'type':'item.completed','item':{'id':'files','type':'file_change','changes':[{'path':'src/app.py'}]}},
            {'type':'item.completed','item':{'id':'reason','type':'reasoning','text':'never-show-private-thoughts'}},
            {'type':'item.completed','item':{'id':'public','type':'agent_message','text':'The tests pass.'}},
        ]:
            self.stream.codex(event)
        encoded = json.dumps(self.events)
        self.assertNotIn('never-show', encoded)
        self.assertIn('src/app.py', encoded)
        self.assertIn('exit 0', encoded)
        self.assertIn('The tests pass.', encoded)

    def test_claude_partial_messages_stream_with_stable_id(self):
        events = [
            {'type':'message_start','message':{'id':'message-1'}},
            {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
            {'type':'content_block_delta','delta':{'type':'text_delta','text':'Reading the files'}},
            {'type':'content_block_delta','delta':{'type':'text_delta','text':' and checking the tests.'}},
            {'type':'content_block_stop'},
        ]
        for event in events:
            self.stream.claude({'type':'stream_event','event':event})
        self.assertGreaterEqual(len(self.events), 2)
        self.assertEqual({e['id'] for e in self.events}, {'message-1:0'})
        self.assertEqual(self.events[-1]['text'], 'Reading the files and checking the tests.')
        self.assertEqual(self.events[-1]['state'], 'completed')

    def test_claude_never_exposes_thinking_or_raw_tool_output(self):
        self.stream.claude({'type':'stream_event','event':{'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':'private-never-show'}}})
        self.stream.claude({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'private-never-show'}}})
        self.stream.claude({'type':'assistant','message':{'id':'m','content':[{'type':'thinking','thinking':'private-never-show'}, {'type':'tool_use','id':'tool','name':'Bash','input':{'command':'private-never-show'}}]}})
        self.stream.claude({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool','content':'private-never-show'}]}})
        self.assertNotIn('private-never-show', json.dumps(self.events))
        self.assertIn('project command', json.dumps(self.events))

    def test_stream_deduplicates_and_coalesces_frequent_updates(self):
        with patch('aabridge_adapter_codex.activity.time.monotonic', return_value=100):
            for index in range(100):
                self.stream.emit('item', 'message', str(index))
            self.stream.emit('item', 'message', '99', 'completed', force=True)
            self.stream.emit('item', 'message', '99', 'completed', force=True)
        self.assertEqual(len(self.events), 2)
        self.assertEqual(self.events[-1]['text'], '99')

    def test_public_text_redacts_tokens_and_limits_size(self):
        self.stream.emit('message', 'message', 'sk-ant-abcdefghijklmno password=hello Bearer secret-token ' + 'x' * 4000)
        value = self.events[0]['text']
        self.assertNotIn('abcdefghijklmno', value)
        self.assertNotIn('password=hello', value)
        self.assertNotIn('secret-token', value)
        self.assertLessEqual(len(value), 2000)

    def test_command_failure_and_tool_progress_are_visible_without_tool_arguments(self):
        self.stream.codex({'type':'item.completed','item':{'id':'c','type':'command_execution','exit_code':1}})
        self.assertEqual(self.events[-1]['state'],'failed')
        self.stream.claude({'type':'assistant','message':{'id':'m','content':[{'type':'tool_use','id':'tool','name':'Read','input':{'file_path':'src/app.py'}}]}})
        with patch('aabridge_adapter_codex.activity.time.monotonic', return_value=1000000000):
            self.stream.claude({'type':'tool_progress','tool_use_id':'tool','elapsed_time_seconds':12})
        self.assertIn('12s',self.events[-1]['text'])
        self.stream.claude({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool'}]}})
        self.assertIn('src/app.py',self.events[-1]['text'])

    def test_claude_reports_resolved_model_without_claiming_default_resolution(self):
        self.stream.claude({'type':'system','subtype':'init','model':'claude-opus-test'})
        self.assertIn('claude-opus-test', self.events[-1]['text'])


class TaskModelSettingsTests(unittest.TestCase):
    def test_model_and_effort_flags_apply_to_new_and_resumed_sessions(self):
        for agent in ('codex','claude'):
            worker = NativeWorker.__new__(NativeWorker)
            worker.project = {'adapter':agent}
            worker.task = {'model_settings':{'model':'test-model','effort':'high'}}
            for resumed in (False, True):
                worker.task['thread_id'] = 'exact-session' if resumed else None
                arguments = worker.arguments()
                self.assertEqual(arguments[arguments.index('--model') + 1], 'test-model')
                self.assertIn('model_reasoning_effort="high"' if agent == 'codex' else '--effort', arguments)
                if resumed: self.assertIn('exact-session', arguments)
                if agent == 'claude': self.assertIn('--include-partial-messages', arguments)

    def test_default_settings_pass_no_model_or_effort_override(self):
        worker = NativeWorker.__new__(NativeWorker)
        for agent in ('codex','claude'):
            worker.project = {'adapter':agent}; worker.task = {}
            args = worker.arguments()
            self.assertNotIn('--model', args); self.assertNotIn('--effort', args)
            self.assertFalse(any('model_reasoning_effort' in item for item in args))

    def test_rejects_option_injection_and_agent_incompatible_effort(self):
        for value, agent in [({'model':'--dangerously-skip-permissions'},'claude'), ({'model':'model; echo hi'},'codex'), ({'effort':'ultracode'},'codex'), ({'effort':'ultra'},'claude'), ({'model':'m','unknown':True},'codex')]:
            with self.subTest(value=value, agent=agent), self.assertRaises(BridgeError): model_settings(value,agent)
        self.assertEqual(model_settings({'model':'opus[1m]','effort':'high'},'claude')['model'], 'opus[1m]')
        self.assertNotIn('activity', MUTATIONS); self.assertNotIn('team_activity', MUTATIONS)

    def test_same_participant_cannot_fill_both_roles_by_changing_model(self):
        value = {'objective':'Build', 'leader':{'target':'local','project':'app','agent':'codex','model_settings':{'model':'a'}},
                 'follower':{'target':'local','project':'app','agent':'codex','model_settings':{'model':'b'}}}
        with self.assertRaises(BridgeError): request('team_start',value)


class ActivityStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp',prefix='aab-activity-'); self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name).resolve()/'state'; initialize(self.state)
        self.config=load_config(self.state)
        self.config['peers']['other']={'enabled':True,'projects':['app'],'digest':'unused'}
        self.config['projects']['app']={'worker':'codex','adapter':'codex','scope':['*'],'base_commit':'a'*40,
             'limits':{'runtime_seconds':60,'max_turns':10,'max_revisions':5},'permission_profile':'coding-v1'}
        self.store=Store(self.state); self.addCleanup(self.store.close)
        p=self.config['projects']['app']
        self.task=self.store.handle(request('submit', {'project_id':'app','recipient':'codex','scope':['*'],'base_commit':'a'*40,
             'objective':'Build','context':[],'acceptance':['Review'],'permission_profile':'coding-v1','limits':p['limits']}),'local',self.config)

    def test_bounded_pages_unicode_and_ownership(self):
        for i in range(50): self.store.record_activity(self.task['id'],{'id':str(i),'kind':'message','text':'😀'*2000,'state':'running'})
        value=self.store.handle(request('activity',{'task_id':self.task['id'],'cursor':0}),'local',self.config)
        self.assertTrue(value['has_more']); self.assertGreater(value['cursor'],0)
        self.assertLess(len(json.dumps(value).encode()),200000)
        with self.assertRaises(BridgeError): self.store.handle(request('activity',{'task_id':self.task['id'],'cursor':0}),'other',self.config)
        next_page=self.store.handle(request('activity',{'task_id':self.task['id'],'cursor':value['cursor']}),'local',self.config)
        self.assertGreater(next_page['cursor'],value['cursor'])

    def test_revision_can_select_and_clear_settings_without_switching_session(self):
        self.store.update(self.task['id'],'result',status='completed',thread_id='same-thread')
        body={'task_id':self.task['id'],'revision':1,'text':'Improve','model_settings':{'model':'test-model','effort':'low'}}
        result=self.store.handle(request('revise',body),'local',self.config)
        self.assertEqual(result['model_settings'],body['model_settings']); self.assertEqual(result['thread_id'],'same-thread')
        self.store.update(self.task['id'],'result',status='completed')
        result=self.store.handle(request('revise',dict(body,revision=2,model_settings={})),'local',self.config)
        self.assertEqual(result['model_settings'],{})
