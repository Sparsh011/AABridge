"""Check the desktop against real bridge output and render every Team screen.

1. Builds room states with the real team code (no agent, service or model runs).
2. Compiles the Swift app for this Mac and runs `--self-test` on that output:
   display rules, and decoding of everything the service returns.
3. Renders each preview screen and fails on a missing or blank image.

Run with `make ui-check`. It takes a minute or two because it compiles Swift.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIEWS = [("running", ["--preview-live"], []), ("done", ["--preview-done"], ["done"]), ("plan-gate", ["--preview-done"], ["gate"]),
            ("question", ["--preview-done"], ["question"]), ("unapproved", ["--preview-done"], ["unapproved"]), ("blocked", ["--preview-done"], ["fail"]), ("mismatch", ["--preview-done"], ["mismatch"]),
            ("new-task", ["--preview-team"], []), ("projects", ["--preview-projects"], []), ("devices", ["--preview-devices"], []),
            ("connections", ["--preview-connections"], ["--outdated"])]


def service_fixture(folder: Path) -> Path:
    """Room states as the service's own `team_status`, `teams` and `team_activity` return them."""
    from aabridge_cli.actions import request
    from aabridge_core import teams
    from aabridge_core.config import initialize, release
    from aabridge_core.store import Store
    from aabridge_protocol.messages import canonical

    state = folder / "state"
    initialize(state)
    store = Store(state)
    work = {"branch": "claude/task-1", "worktree": "/tmp/aabridge-fixture", "result_commit": "a" * 40, "base_commit": "b" * 40, "summary": "Built it.", "changed_paths": ["app.py"]}

    def make(name: str, **changes) -> dict:
        body = {"objective": "Add a weight input screen", "approval": "auto", "placement": {"mode": "branch", "pull_request": True},
                "leader": {"target": "mini", "agent": "codex", "project": "app"}, "follower": {"target": "local", "agent": "claude", "project": "app"}}
        started = store.handle(request("team_start", body, "ui-" + name), "local", {})
        room = teams.get(store, started["id"])
        room.update(changes)
        teams.save(store, room)
        return store.handle(request("team_status", {"team_id": room["id"]}), "local", {})

    rooms = {
        "question": make("question", status="awaiting_admin", stage="work", task_id="task-1",
                         gate={"kind": "question", "cause": "question", "reason": "Kilograms or pounds?", "question_id": "q1", "task_id": "task-1", "revision": 1}),
        "dispute": make("dispute", status="awaiting_admin", stage="review", fixes=1, verdict="changes", outputs={"work": work},
                        gate={"kind": "review", "cause": "dispute", "reason": "The worker changed no code and the leader still wants changes."}),
        "unapproved": make("unapproved", status="completed", stage="review", verdict="changes", outputs={"work": work}),
        "answered": make("answered", status="completed", stage="plan", answered=True, verdict="approved", outputs={"work": work}, previous_result=work),
        "building": make("building", status="running", stage="work", stage_started=time.time() - 90,
                         preview={"branch": "aabridge-live/abc123", "commit": "c" * 40, "files": 3, "time": time.time()},
                         usage={"leader": {"mini/task-0/1": {"input": 1200, "output": 300, "cached": 900, "cost": 0.0125}}},
                         live={"role": "follower", "agent": "claude", "target": "local", "stage": "work", "task_id": "task-1", "status": "running",
                               "checked_at": time.time(), "started_at": time.time() - 90, "activity": None, "supported": True, "model_settings": {}}),
        "delivered": make("delivered", status="completed", stage="review", verdict="approved", outputs={"work": work},
                          publication={"mode": "branch", "branch": "claude/weight-screen-abc123", "path": "/tmp/project", "commit": "a" * 40, "repository": "/tmp/project", "error": "",
                                       "pull_request": "https://github.com/o/r/pull/7", "review_posts": {"https://github.com/o/r/pull/7": "review"}}),
    }
    rooms["mismatch"] = make("mismatch", status="blocked", stage="plan", error="The two project copies are on different Git commits.", error_code="baseline_mismatch")
    long_room = teams.get(store, make("long")["id"])
    for number in range(200):
        teams.message(long_room, "AABridge", "Note " + str(number))
    teams.save(store, long_room)
    rooms["long"] = store.handle(request("team_status", {"team_id": long_room["id"]}), "local", {})
    building = rooms["building"]["id"]
    entry = {"id": "local/task-1/1/tool-1", "kind": "tool", "text": "Reading a file: app.py", "state": "running", "task_id": "task-1", "revision": 1, "time": time.time(),
             "role": "leader", "agent": "codex", "target": "mini", "stage": "plan"}
    store.db.execute("INSERT INTO team_activity(team_id,source,source_cursor,document) VALUES (?,?,?,?)", (building, "local/task-1", 1, canonical(entry)))
    fixture = {"rooms": rooms, "list": store.handle(request("teams", {}), "local", {})["teams"],
               "activity": store.handle(request("team_activity", {"team_id": building, "cursor": 0}), "local", {}),
               # The shape `device_status` returns; it is not called here because it would start a service.
               "devices": {"service": True, "listening": True, "service_error": None, "app_release": release(),
                           "service_release": store.handle(request("ping", {}), "local", {"node_id": "fixture"})["release"],
                           "devices": [{"id": "mini", "label": "Mac mini", "host": "Mac-mini.local", "online": True, "release": "older"}]}}
    store.close()
    path = folder / "service-output.json"
    path.write_text(json.dumps(fixture))
    return path


def build(folder: Path) -> Path:
    """A minimal bundle around a debug build for this Mac's architecture."""
    contents = folder / "AABridge.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "AABridge", "CFBundleIdentifier": "local.aabridge.desktop.uicheck",
                                                          "CFBundleName": "AABridge", "CFBundlePackageType": "APPL", "LSMinimumSystemVersion": "13.0"}))
    binary = contents / "MacOS" / "AABridge"
    subprocess.run(["/usr/bin/xcrun", "swiftc", "-swift-version", "5", "-parse-as-library", "-Onone", "-target", platform.machine() + "-apple-macosx13.0",
                    "-module-cache-path", str(folder / "modules"), "-framework", "SwiftUI", "-framework", "AppKit",
                    *map(str, sorted((ROOT / "apps/desktop").glob("*.swift"))), "-o", str(binary)], check=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(contents.parent)], check=True, capture_output=True)
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="Keep the rendered screens in this folder.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="aab-ui-", dir="/private/tmp") as temporary:
        folder = Path(temporary).resolve()
        fixture = service_fixture(folder)
        binary = build(folder)
        checks = subprocess.run([str(binary), "--self-test", str(fixture)], capture_output=True, text=True)
        print(checks.stdout.strip())
        failed = [] if checks.returncode == 0 else ["self-test"]
        screens = args.output.absolute() if args.output else folder / "screens"
        screens.mkdir(parents=True, exist_ok=True)
        for name, flag, extra in PREVIEWS:
            image = screens / (name + ".png")
            image.unlink(missing_ok=True)
            outcome = subprocess.run([str(binary), *flag, str(image), *extra], capture_output=True, timeout=120)
            # A blank 2400x1640 capture compresses to a few kilobytes; a drawn screen does not.
            if outcome.returncode != 0 or not image.is_file() or image.stat().st_size < 20_000:
                failed.append("screen " + name)
        print(json.dumps({"screens": len(PREVIEWS), "failed": failed, **({"saved": str(screens)} if args.output else {})}))
        return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from source_paths import source_environment
    if os.environ.get("AABRIDGE_UI_CHECK") != "1":
        # Re-run with this checkout's packages on the path.
        raise SystemExit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], env={**source_environment(ROOT), "AABRIDGE_UI_CHECK": "1"}))
    raise SystemExit(main())
