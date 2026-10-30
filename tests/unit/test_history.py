from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from aabridge_cli.actions import demo_init, request
from aabridge_core.config import initialize, load_config
from aabridge_core.store import Store
from aabridge_protocol.messages import BridgeError


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="aab-history-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        initialize(self.state)
        demo_init(self.state, self.root / "source", "demo")
        self.config = load_config(self.state)
        project = self.config["projects"]["demo"]
        self.body = {"project_id": "demo", "recipient": "codex", "base_commit": project["base_commit"], "objective": "Fix add",
                     "scope": project["scope"], "acceptance": ["Return the sum"], "context": ["Integer inputs"],
                     "limits": project["limits"], "permission_profile": "proposal-v1"}
        self.store = Store(self.state)
        self.addCleanup(lambda: self.store.close())

    def submit(self, key="submission-one", owner="local"):
        return self.store.handle(request("submit", self.body, key), owner, self.config)

    def messages(self, task_id, identity="local"):
        cursor, messages = 0, []
        while True:
            reply = self.store.handle(request("conversation", {"task_id": task_id, "cursor": cursor}), identity, self.config)
            self.assertLess(len(json.dumps(reply).encode()), 262144)
            messages.extend(reply["messages"])
            if not reply["has_more"]:
                return messages
            self.assertGreater(reply["cursor"], cursor)
            cursor = reply["cursor"]

    def test_question_answer_and_result_survive_revision_restart_and_retries(self):
        task = self.submit()
        self.store.update(task["id"], "question", status="waiting_question", question={"id": "question-one", "text": "Integer addition?"})
        answer = request("answer", {"task_id": task["id"], "revision": 1, "question_id": "question-one", "text": "Yes, addition."}, "answer-once")
        self.store.handle(answer, "local", self.config)
        self.store.handle(answer, "local", self.config)
        self.store.record_message(task["id"], "worker", "proposal", '{"summary":"First proposal"}')
        self.store.update(task["id"], "result", status="completed", pending=None, result={"summary": "First verified result"})
        self.store.handle(request("revise", {"task_id": task["id"], "revision": 1, "text": "Add a docstring."}, "revision-once"), "local", self.config)
        before = self.messages(task["id"])
        self.store.close()
        self.store = Store(self.state)
        after = self.messages(task["id"])
        self.assertEqual(before, after)
        self.assertEqual([m["text"] for m in after if m["kind"] == "question"], ["Integer addition?"])
        self.assertEqual([m["text"] for m in after if m["kind"] == "answer"], ["Yes, addition."])
        self.assertTrue(any("First verified result" in m["text"] for m in after))
        self.assertEqual(after[-1]["text"], "Add a docstring.")
        self.assertEqual([m["cursor"] for m in after], sorted(m["cursor"] for m in after))
        self.assertFalse(any(m["recovered"] for m in after))

    def test_history_write_failure_rolls_back_task_and_idempotency_record(self):
        self.store.db.execute("CREATE TRIGGER reject_history BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT, 'fixture storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.submit()
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)

    def test_history_cannot_be_rewritten_or_deleted(self):
        self.submit()
        for statement in ("UPDATE messages SET document='{}'", "DELETE FROM messages"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(statement)

    def test_task_listing_and_history_enforce_owner_project_and_revocation(self):
        for peer in ("alice", "bob"):
            self.config["peers"][peer] = {"enabled": True, "projects": ["demo"]}
        task = self.submit(owner="alice")
        self.assertEqual(self.store.handle(request("tasks", {"cursor": 0}), "bob", self.config)["tasks"], [])
        with self.assertRaises(BridgeError):
            self.messages(task["id"], "bob")
        self.assertTrue(self.messages(task["id"], "alice"))
        self.config["peers"]["alice"]["projects"] = []
        self.assertEqual(self.store.handle(request("tasks", {"cursor": 0}), "alice", self.config)["tasks"], [])
        with self.assertRaises(BridgeError):
            self.messages(task["id"], "alice")
        self.config["peers"]["alice"]["enabled"] = False
        with self.assertRaises(BridgeError):
            self.store.handle(request("tasks", {"cursor": 0}), "alice", self.config)

    def test_large_unicode_context_is_lossless_and_responses_stay_bounded(self):
        task = self.submit()
        original = "🌉" * 100000
        self.store.record_message(task["id"], "context", "prepared_model_input", original)
        parts = [m for m in self.messages(task["id"]) if m["kind"] == "prepared_model_input"]
        self.assertEqual("".join(m["text"] for m in parts), original)
        self.assertEqual([m["part"] for m in parts], list(range(1, len(parts) + 1)))

    def test_legacy_migration_preserves_task_and_labels_unrecoverable_history(self):
        task = self.submit()
        self.store.update(task["id"], "fixture", pending={"kind": "answer", "text": "Retained old answer"})
        completed = self.submit(key="completed-legacy")
        self.store.update(completed["id"], "fixture", status="completed", result={"summary": "Retained verified result"})
        self.store.db.execute("DROP TABLE messages")
        self.store.db.execute("PRAGMA user_version=1")
        original = self.store.get(task["id"])
        self.store.close()
        self.store = Store(self.state)
        self.assertEqual(original, self.store.get(task["id"]))
        messages = self.messages(task["id"])
        self.assertEqual(messages[0]["kind"], "history_notice")
        self.assertTrue(all(m["recovered"] for m in messages))
        self.assertEqual(messages[-1]["text"], "Retained old answer")
        self.assertFalse(any(m["kind"] == "question" for m in messages))
        result = next(m for m in self.messages(completed["id"]) if m["kind"] == "verified_result")
        self.assertEqual(result["role"], "system")
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_task_pagination_does_not_skip_or_repeat_tasks(self):
        ids = [self.submit(key=f"submission-{i}")["id"] for i in range(35)]
        first = self.store.handle(request("tasks", {"cursor": 0}), "local", self.config)
        second = self.store.handle(request("tasks", {"cursor": first["cursor"]}), "local", self.config)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual([task["id"] for task in first["tasks"] + second["tasks"]], ids[::-1])

    def test_human_and_agent_provenance_survives_followups_without_relabelling_older_messages(self):
        self.body["origin"] = {"kind": "human", "name": "You"}
        task = self.submit()
        self.store.update(task["id"], "question", status="waiting_question", question={"id": "question-one", "text": "Integer addition?"})
        self.store.handle(request("answer", {"task_id": task["id"], "revision": 1, "question_id": "question-one", "text": "Yes",
                                               "origin": {"kind": "agent", "name": "Codex coordinator"}}), "local", self.config)
        self.store.record_message(task["id"], "worker", "proposal", '{"summary":"Fixed addition"}')
        messages = self.messages(task["id"])
        original = next(m for m in messages if m["kind"] == "task")
        question = next(m for m in messages if m["kind"] == "question")
        answer = next(m for m in messages if m["kind"] == "answer")
        proposal = next(m for m in messages if m["kind"] == "proposal")
        self.assertEqual(original["actor"], {"kind": "human", "name": "You"})
        self.assertEqual(question["actor"]["name"], "codex")
        self.assertEqual(question["recipient"], original["actor"])
        self.assertEqual(answer["actor"]["name"], "Codex coordinator")
        self.assertEqual(proposal["recipient"], answer["actor"])
        self.assertTrue(all(m["sender_identity"] == "local" for m in messages))

    def test_claimed_human_origin_cannot_grant_project_access(self):
        self.body["origin"] = {"kind": "human", "name": "local administrator"}
        self.config["peers"]["unassigned"] = {"enabled": True, "projects": []}
        with self.assertRaises(BridgeError):
            self.submit(owner="unassigned")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_malformed_origin_is_rejected(self):
        for origin in ({"kind": [], "name": "You"}, {"kind": "human", "name": "You", "permissions": "*"}, {"kind": "human", "name": "x" * 81}):
            with self.subTest(origin=origin), self.assertRaises(BridgeError):
                request("submit", {**self.body, "origin": origin})

    def test_local_operator_reply_records_actual_sender_without_changing_task_owner(self):
        self.config["peers"]["macbook"] = {"enabled": True, "projects": ["demo"]}
        task = self.submit(owner="macbook")
        self.store.update(task["id"], "question", status="waiting_question", question={"id": "question-one", "text": "Integer addition?"})
        self.store.handle(request("answer", {"task_id": task["id"], "revision": 1, "question_id": "question-one", "text": "Yes",
                                               "origin": {"kind": "human", "name": "You"}}), "local", self.config)
        answer = next(m for m in self.messages(task["id"]) if m["kind"] == "answer")
        self.assertEqual(answer["sender_identity"], "local")
        self.assertEqual(answer["owner_identity"], "macbook")
        self.assertEqual(self.store.get(task["id"])["sender"], "macbook")

    def coding(self):
        self.config['projects']['demo'].update(permission_profile='coding-v1', scope=['*'])
        self.body.update(permission_profile='coding-v1', scope=['*'])
        self.config['peers']['paired'] = {'enabled': True, 'projects': ['demo']}

    def test_shared_local_coding_history_is_read_only_and_explicit(self):
        self.coding()
        task = self.submit()
        owned = self.store.handle(request('tasks', {'cursor': 0}), 'paired', self.config)
        self.assertEqual(owned['tasks'], [])
        shared = self.store.handle(request('tasks', {'cursor': 0, 'shared': True}), 'paired', self.config)
        self.assertEqual([t['id'] for t in shared['tasks']], [task['id']])
        self.assertFalse(shared['tasks'][0]['permissions']['can_control'])
        for op, body in [('status', {'task_id': task['id']}),
                         ('conversation', {'task_id': task['id'], 'cursor': 0}),
                         ('events', {'task_id': task['id'], 'cursor': 0}),
                         ('activity', {'task_id': task['id'], 'cursor': 0})]:
            with self.subTest(op=op):
                with self.assertRaises(BridgeError):
                    self.store.handle(request(op, body), 'paired', self.config)
                reply = self.store.handle(request(op, {**body, 'shared': True}), 'paired', self.config)
                self.assertIsInstance(reply, dict)
        for op, body in [('cancel', {'task_id': task['id']}),
                         ('answer', {'task_id': task['id'], 'revision': 1, 'question_id': 'question-one', 'text': 'No'}),
                         ('revise', {'task_id': task['id'], 'revision': 1, 'text': 'Change it'})]:
            with self.assertRaises(BridgeError) as denied:
                self.store.handle(request(op, body), 'paired', self.config)
            self.assertEqual(denied.exception.code, 'permission_denied')
            with self.assertRaises(BridgeError):
                request(op, {**body, 'shared': True})
        self.assertEqual(self.store.get(task['id']), task)

    def test_shared_reads_do_not_expose_proposals_or_another_peers_tasks(self):
        self.config['peers']['paired'] = {'enabled': True, 'projects': ['demo']}
        proposal = self.submit('local-proposal')
        self.assertEqual(self.store.handle(request('tasks', {'cursor': 0, 'shared': True}), 'paired', self.config)['tasks'], [])
        self.coding()
        self.config['peers']['other'] = {'enabled': True, 'projects': ['demo']}
        other = self.submit('other-owner', owner='other')
        for task in (proposal, other):
            with self.assertRaises(BridgeError):
                self.store.handle(request('status', {'task_id': task['id'], 'shared': True}), 'paired', self.config)
        self.assertEqual(self.store.handle(request('tasks', {'cursor': 0, 'shared': True}), 'paired', self.config)['tasks'], [])

    def test_shared_history_rechecks_project_grants_and_revocation(self):
        self.coding(); task = self.submit()
        body = {'task_id': task['id'], 'cursor': 0, 'shared': True}
        self.assertTrue(self.store.handle(request('conversation', body), 'paired', self.config)['messages'])
        self.config['peers']['paired']['projects'] = []
        self.assertEqual(self.store.handle(request('tasks', {'cursor': 0, 'shared': True}), 'paired', self.config)['tasks'], [])
        with self.assertRaises(BridgeError):
            self.store.handle(request('conversation', body), 'paired', self.config)
        self.config['peers']['paired']['projects'] = ['demo']
        self.config['peers']['paired']['enabled'] = False
        with self.assertRaises(BridgeError):
            self.store.handle(request('conversation', body), 'paired', self.config)

    def test_shared_history_pages_without_skipping_local_or_owned_tasks(self):
        self.coding()
        expected = []
        for index in range(66):
            expected.append(self.submit('page-' + str(index), owner='local' if index % 2 else 'paired')['id'])
        cursor, seen = 0, []
        while True:
            page = self.store.handle(request('tasks', {'cursor': cursor, 'shared': True}), 'paired', self.config)
            seen += [task['id'] for task in page['tasks']]
            if not page['has_more']:
                break
            self.assertLess(page['cursor'], cursor or 2**63)
            cursor = page['cursor']
        self.assertEqual(seen, list(reversed(expected)))

    def test_shared_flag_is_strict_boolean_and_cannot_be_sent_on_mutations(self):
        for value in (1, 'true', None, []):
            with self.assertRaises(BridgeError):
                request('tasks', {'cursor': 0, 'shared': value})
