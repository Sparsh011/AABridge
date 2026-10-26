from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from aabridge_protocol.messages import BridgeError, MUTATIONS, Request, canonical, require
from .config import authorize, read_private, write_private


def new_id(prefix: str) -> str:
    return prefix + "-" + secrets.token_hex(12)


class Store:
    def __init__(self, state: Path):
        path = state / "bridge.sqlite3"
        if not path.exists():
            write_private(path, b"", exclusive=True)
        read_private(path, maximum=0) if path.stat().st_size == 0 else self._validate_database(path)
        for suffix in ("-journal", "-wal", "-shm"):
            auxiliary = state / ("bridge.sqlite3" + suffix)
            if auxiliary.exists() or auxiliary.is_symlink():
                self._validate_database(auxiliary)
        self.db = sqlite3.connect(path, isolation_level=None)
        if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
            self.db.close()
            raise BridgeError("unsupported_version", "Database version is newer than this bridge; nothing was migrated.")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS activity (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS activity_task ON activity(task_id,seq);
            CREATE TABLE IF NOT EXISTS team_activity (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id TEXT NOT NULL, source TEXT NOT NULL, source_cursor INTEGER NOT NULL, document TEXT NOT NULL,
                UNIQUE(team_id,source,source_cursor));
            CREATE INDEX IF NOT EXISTS activity_team ON team_activity(team_id,seq);
            CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project TEXT NOT NULL,
                owner TEXT NOT NULL, status TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, kind TEXT NOT NULL, document TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id,seq);
            CREATE TABLE IF NOT EXISTS requests (owner TEXT NOT NULL, key TEXT NOT NULL,
                digest TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(owner,key));
        """)
        self._migrate_history()
        from .teams import setup
        setup(self.db)

    def _migrate_history(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("PRAGMA user_version").fetchone()[0] < 2:
                self.db.execute("CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, document TEXT NOT NULL)")
                self.db.execute("CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id,seq)")
                self.db.execute("CREATE TRIGGER IF NOT EXISTS messages_no_update BEFORE UPDATE ON messages BEGIN SELECT RAISE(ABORT, 'Conversation messages are append-only'); END")
                self.db.execute("CREATE TRIGGER IF NOT EXISTS messages_no_delete BEFORE DELETE ON messages BEGIN SELECT RAISE(ABORT, 'Conversation messages are append-only'); END")
                for (document,) in self.db.execute("SELECT document FROM tasks").fetchall():
                    task = json.loads(document)
                    self.append_message(task, "system", "history_notice", "This task predates permanent conversation history. The entries below were recovered from retained state; earlier messages may be missing. Original exchange order cannot be reconstructed completely.", recovered=True)
                    self.append_message(task, "coordinator", "task", task["objective"], recovered=True)
                    self.append_message(task, "context", "task_context", json.dumps({key: task[key] for key in
                        ("project_id", "base_commit", "scope", "acceptance", "context", "limits", "permission_profile")}, indent=2), recovered=True)
                    if task.get("question"):
                        self.append_message(task, "worker", "question", task["question"]["text"], recovered=True)
                    if task.get("pending"):
                        self.append_message(task, "coordinator", task["pending"]["kind"], task["pending"]["text"], recovered=True)
                    if task.get("result"):
                        self.append_message(task, "system", "verified_result", json.dumps(task["result"], indent=2), recovered=True)
                self.db.execute("PRAGMA user_version=2")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            self.db.close()
            raise

    def append_message(self, task: dict, role: str, kind: str, content: str, *, recovered=False):
        require(self.db.in_transaction, "History must be committed with its task state.")
        # Chunk large source/context records so every RPC page stays bounded.
        chunks = [content[index:index + 8000] for index in range(0, len(content), 8000)] or [""]
        group = new_id("message")
        # Display provenance only. Authorization always uses the authenticated
        # task sender, never an asserted human/agent label from a request body.
        coordinator = task.get("last_actor") or {"kind": "coordinator", "name": task["sender"]}
        worker = {"kind": "agent", "name": task["recipient"]}
        actor = worker if role == "worker" else coordinator if role == "coordinator" else {"kind": "system", "name": "AABridge"}
        recipient = coordinator if role == "worker" else worker if role == "coordinator" else None
        for part, chunk in enumerate(chunks, 1):
            document = {"id": group, "task_id": task["id"], "revision": task["revision"], "attempt_id": task["attempt_id"],
                        "role": role, "kind": kind, "text": chunk, "part": part, "parts": len(chunks),
                        "time": time.time(), "recovered": recovered, "actor": actor, "recipient": recipient,
                        "sender_identity": task.get("last_sender_identity", task["sender"]), "owner_identity": task["sender"]}
            self.db.execute("INSERT INTO messages(task_id,document) VALUES (?,?)", (task["id"], canonical(document)))

    def history_page(self, task_id: str, cursor: int) -> dict:
        messages, size = [], 0
        for seq, document in self.db.execute("SELECT seq,document FROM messages WHERE task_id=? AND seq>? ORDER BY seq LIMIT 32", (task_id, cursor)):
            if size + len(document.encode()) > 180000:
                break
            messages.append({"cursor": seq, **json.loads(document)})
            size += len(document.encode())
        next_cursor = messages[-1]["cursor"] if messages else cursor
        more = self.db.execute("SELECT 1 FROM messages WHERE task_id=? AND seq>? LIMIT 1", (task_id, next_cursor)).fetchone() is not None
        return {"messages": messages, "cursor": next_cursor, "has_more": more}

    def record_message(self, task_id: str, role: str, kind: str, content: str):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.append_message(self.get(task_id), role, kind, content)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def _validate_database(path: Path) -> None:
        # Read metadata through a no-follow descriptor without reading DB contents.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            import stat
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077, "Unsafe database ownership/type/permissions.", "unsafe_state")
        finally:
            os.close(descriptor)

    def close(self):
        self.db.close()

    def get(self, task_id: str) -> dict:
        row = self.db.execute("SELECT document FROM tasks WHERE id=?", (task_id,)).fetchone()
        require(row is not None, "Task not found or not accessible.", "not_found")
        return json.loads(row[0])

    def save(self, task: dict, kind: str, details: dict | None = None) -> None:
        task["updated_at"] = time.time()
        self.db.execute("INSERT INTO tasks VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document",
                        (task["id"], task["project_id"], task["sender"], task["status"], canonical(task)))
        event = {"version": 1, "task_id": task["id"], "attempt_id": task["attempt_id"], "revision": task["revision"],
                 "status": task["status"], "time": task["updated_at"], **(details or {})}
        self.db.execute("INSERT INTO events(task_id,kind,document) VALUES (?,?,?)", (task["id"], kind, canonical(event)))
        if kind == "submitted":
            self.append_message(task, "coordinator", "task", task["objective"])
            context = {key: task[key] for key in ("project_id", "base_commit", "scope", "acceptance", "context", "limits", "permission_profile")}
            self.append_message(task, "context", "task_context", json.dumps(context, indent=2))
        elif kind == "question":
            self.append_message(task, "worker", "question", task["question"]["text"])
        elif kind in {"answer_received", "revision_requested"}:
            self.append_message(task, "coordinator", "answer" if kind == "answer_received" else "revision", task["pending"]["text"])
        elif kind == "result":
            review = any(item.get("review_required") for item in (task.get("result") or {}).get("verification", []))
            self.append_message(task, "system", "review_required_result" if review else "verified_result", json.dumps(task["result"], indent=2))
        elif kind in {"failed", "limit_exceeded", "cancelled", "cancellation_requested", "interrupted", "restart_interrupted", "locally_reconciled"}:
            self.append_message(task, "system", kind, task.get("error") or kind.replace("_", " ").capitalize())

    def update(self, task_id: str, kind: str, **changes) -> dict:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            task = self.get(task_id)
            task.update(changes)
            self.save(task, kind)
            self.db.execute("COMMIT")
            return task
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def record_activity(self, task_id: str, event: dict):
        task = self.get(task_id)
        document = {**event, "task_id": task_id, "revision": task["revision"], "time": time.time()}
        self.db.execute("INSERT INTO activity(task_id,document) VALUES (?,?)", (task_id, canonical(document)))
        return document

    def activity_page(self, task_id: str, cursor: int) -> dict:
        rows = self.db.execute("SELECT seq,document FROM activity WHERE task_id=? AND seq>? ORDER BY seq LIMIT 32", (task_id, cursor)).fetchall()
        entries, size = [], 0
        for seq, document in rows:
            if size + len(document.encode()) > 180000:
                break
            entries.append({**json.loads(document), "cursor": seq})
            size += len(document.encode())
        next_cursor = entries[-1]["cursor"] if entries else cursor
        more = self.db.execute("SELECT 1 FROM activity WHERE task_id=? AND seq>?", (task_id, next_cursor)).fetchone() is not None
        return {"entries": entries, "cursor": next_cursor, "has_more": more}

    def handle(self, request: Request, identity: str, config: dict) -> dict:
        if request.op.startswith("team"):
            from .teams import handle
            return handle(self, request, identity)
        if request.op == "ping":
            return {"version": 1, "node_id": config["node_id"], "profile": "proposal-v1"}
        if request.op == "projects":
            peer = config["peers"][identity]
            allowed = {name: project for name, project in config["projects"].items()
                       if "*" in peer["projects"] or name in peer["projects"]}
            return {name: {"capabilities": {"activity": True, "model_settings": project.get("permission_profile") == "coding-v1"}, **{key: project[key] for key in ("worker", "adapter", "scope", "base_commit", "limits", "verification", "execution_enabled")}}
                    for name, project in allowed.items()}
        if request.op == "tasks":
            peer = config["peers"].get(identity, {})
            require(peer.get("enabled") is True, "Coordinator access is revoked.", "permission_denied")
            cursor, output = request.body["cursor"], []
            query = "SELECT rowid,document FROM tasks WHERE (?=0 OR rowid<?) AND (?='local' OR owner=?) ORDER BY rowid DESC"
            for seq, document in self.db.execute(query, (cursor, cursor, identity, identity)):
                task = json.loads(document)
                if task["project_id"] not in config["projects"] or ("*" not in peer["projects"] and task["project_id"] not in peer["projects"]):
                    continue
                if len(output) == 32:
                    return {"tasks": output, "cursor": output[-1]["cursor"], "has_more": True}
                summary = {key: task[key] for key in ("id", "project_id", "status", "recipient", "sender", "revision", "created_at", "updated_at")}
                output.append({**summary, "objective": task["objective"][:300], "cursor": seq})
            return {"tasks": output, "cursor": output[-1]["cursor"] if output else cursor, "has_more": False}
        require(request.op != "shutdown" or identity == "local", "Only the local operator may stop the daemon.", "permission_denied")
        if request.op == "shutdown":
            return {"stopping": True}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            # Authorize even an idempotency-cache hit, so revocation takes effect.
            if request.op == "submit":
                project = authorize(config, identity, request.body["project_id"])
            else:
                task = self.get(request.body["task_id"])
                project = authorize(config, identity, task["project_id"])
                require(identity == "local" or task["sender"] == identity, "Task access denied.", "permission_denied")
            prior = self.db.execute("SELECT digest,response FROM requests WHERE owner=? AND key=?", (identity, request.key)).fetchone()
            if prior and request.op in MUTATIONS:
                require(prior[0] == request.digest, "Idempotency key was reused with different content.", "replay_conflict")
                result = json.loads(prior[1])
            else:
                result = self._apply(request, identity, project, config)
                if request.op in MUTATIONS:
                    self.db.execute("INSERT INTO requests VALUES (?,?,?,?)", (identity, request.key, request.digest, canonical(result)))
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _apply(self, request: Request, identity: str, project: dict, config: dict) -> dict:
        body, op = request.body, request.op
        if op == "submit":
            require(body["base_commit"] == project["base_commit"], "Base commit is not the locally approved project baseline.", "permission_denied")
            require(body["permission_profile"] == project.get("permission_profile", "proposal-v1"), "Task execution mode differs from the locally enabled project.", "permission_denied")
            require(body["recipient"] == project["worker"], "Worker is not assigned to this project.", "permission_denied")
            require(set(body["scope"]) <= set(project["scope"]), "Task scope exceeds local policy.", "permission_denied")
            for key, value in body["limits"].items():
                require(value <= project["limits"][key], "Resource limit exceeds local policy.", "permission_denied")
            task = {**body, "version": 1, "id": new_id("task"), "attempt_id": new_id("attempt"), "sender": identity,
                    "recipient_node": config["node_id"], "revision": 1, "status": "queued", "turns": 0,
                    "session_id": None, "thread_id": None, "question": None, "result": None,
                    "pending": None, "worker_pid": None, "runtime_used": 0.0, "created_at": time.time()}
            task["last_actor"] = body.get("origin", {"kind": "coordinator", "name": identity})
            task["last_sender_identity"] = identity
            self.save(task, "submitted")
            return task
        task = self.get(body["task_id"])
        if op == "status":
            return task
        if op == "activity":
            return self.activity_page(task["id"], body["cursor"])
        if op == "conversation":
            return self.history_page(task["id"], body["cursor"])
        if op == "events":
            rows = self.db.execute("SELECT seq,kind,document FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT 64", (task["id"], body["cursor"])).fetchall()
            return {"events": [{"cursor": row[0], "kind": row[1], **json.loads(row[2])} for row in rows],
                    "cursor": rows[-1][0] if rows else body["cursor"]}
        if op == "cancel":
            if task["status"] not in {"cancelled", "completed", "failed", "interrupted"}:
                task["status"] = "cancelling" if task["status"] == "running" else "cancelled"
                self.save(task, "cancellation_requested")
            return task
        require(body["revision"] == task["revision"], "Stale task revision.", "stale_revision")
        task["last_actor"] = body.get("origin", {"kind": "coordinator", "name": identity})
        task["last_sender_identity"] = identity
        if "model_settings" in body:
            from aabridge_protocol.messages import model_settings
            require(task["permission_profile"] == "coding-v1", "Model selection requires a full coding project.")
            task["model_settings"] = model_settings(body["model_settings"], project["adapter"])
        if op == "answer":
            require(task["status"] == "waiting_question" and task["question"]["id"] == body["question_id"], "Question is stale or belongs to another task.", "stale_question")
            task["pending"] = {"kind": "answer", "question_id": body["question_id"], "text": body["text"]}
            task["question"] = None
        elif op == "revise":
            require(task["status"] == "completed", "Only a completed result can be revised.", "invalid_state")
            require(task["revision"] <= task["limits"]["max_revisions"], "Revision limit reached.", "limit_exceeded")
            task["revision"] += 1
            task["attempt_id"] = new_id("attempt")
            task["pending"] = {"kind": "revision", "text": body["text"]}
            task["result"] = None
        task["status"] = "queued"
        self.save(task, "answer_received" if op == "answer" else "revision_requested")
        return task

    def queued(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT document FROM tasks WHERE status='queued' ORDER BY rowid")]

    def recover(self) -> None:
        for (task_id,) in self.db.execute("SELECT id FROM tasks WHERE status IN ('running','cancelling')").fetchall():
            self.update(task_id, "restart_interrupted", status="interrupted", error="Supervisor restarted. No replacement worker was started; local reconciliation is required.")

    def project_interrupted(self, project_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tasks WHERE project=? AND status='interrupted'", (project_id,)).fetchone() is not None
