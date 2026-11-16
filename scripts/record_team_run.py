"""Write an evidence file for a team task you ran in the app.

Run a real task in AABridge first (ideally leader and worker on different
Macs), then run this on the admin Mac. It only reads: the saved task, the
versions each Mac reports, and the recorded activity. It starts no agent and
changes nothing in AABridge. The file says plainly whether the run counts as a
two-Mac run, so a fixture or a one-Mac run cannot be mistaken for one.

    python scripts/record_team_run.py                 # the latest task
    python scripts/record_team_run.py --team team-…   # a specific one
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stamp(value) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value)) if value else "unknown"


def record(state: Path, team: str | None = None, ping: bool = True) -> str:
    from aabridge_core.config import load_config, release
    from aabridge_core.teams import shown_title, usage_totals

    try:
        database = sqlite3.connect("file:" + str(state / "bridge.sqlite3") + "?mode=ro", uri=True)
    except sqlite3.OperationalError:
        raise SystemExit("AABridge has no saved tasks in " + str(state) + ". Set up this Mac and run a team task first.") from None
    try:
        rows = database.execute("SELECT id,document FROM teams ORDER BY rowid DESC").fetchall()
        if team:
            rows = [row for row in rows if row[0] == team]
        if not rows:
            raise SystemExit("No matching team task is saved in " + str(state))
        room = json.loads(rows[0][1])
        activity = {}
        for (document,) in database.execute("SELECT document FROM team_activity WHERE team_id=?", (room["id"],)):
            entry = json.loads(document)
            activity[entry["role"]] = activity.get(entry["role"], 0) + 1
    finally:
        database.close()
    config = load_config(state)
    remotes = config.get("remotes", {})
    name = lambda target: "this Mac" if target == "local" else remotes.get(target, {}).get("label") or target
    versions = {"this checkout": release()}
    if ping:
        from aabridge_cli.actions import request, send
        for target in sorted({"local", room["leader"]["target"], room["follower"]["target"]}):
            try:
                versions["service on " + name(target)] = send(state, request("ping", {}), None if target == "local" else target).get("release", "older, does not report a version")
            except Exception as exc:
                versions["service on " + name(target)] = "not reachable now (" + getattr(exc, "message", type(exc).__name__) + ")"
    stages = room.get("stage_tasks", [])
    macs = {room["leader"]["target"], room["follower"]["target"]}
    reported = {value for key, value in versions.items() if key.startswith("service on ")}
    finished = room["status"] == "completed" and {"plan", "work", "review"} <= {item["stage"] for item in stages}
    two_macs = len(macs) == 2
    lines = ["# Team run · " + stamp(room.get("created_at")), "",
             "Recorded by `scripts/record_team_run.py` on " + stamp(time.time()) + " from the saved task. Read-only; nothing was started or changed.", "",
             "## What this run shows", "",
             "- **Two Macs:** " + ("yes, " + name(room["leader"]["target"]) + " led and " + name(room["follower"]["target"]) + " built."
                                  if two_macs else "no. Leader and worker both ran on " + name(room["leader"]["target"]) + ", so this is not a two-Mac run."),
             "- **Full cycle:** " + ("yes, plan, build and review all ran and the task completed." if finished else "no, the task is `" + room["status"] + "` at stage `" + room["stage"] + "`."),
             "- **Same version everywhere:** " + ("not checked." if not ping else "yes." if len(reported) == 1 and not any("not reachable" in v or "older" in v for v in reported) else "no, see Versions."),
             "- **Counts as a release check:** " + ("yes." if two_macs and finished and ping and len(reported) == 1 else "no."), "",
             "## Task", "",
             "- Title: " + shown_title(room), "- Id: `" + room["id"] + "`",
             "- Leader: " + room["leader"]["agent"] + " on " + name(room["leader"]["target"]) + ", project `" + room["leader"]["project"] + "`",
             "- Worker: " + room["follower"]["agent"] + " on " + name(room["follower"]["target"]) + ", project `" + room["follower"]["project"] + "`",
             "- Mode: " + str(room.get("approval") or "single review") + ", placement " + (room.get("placement") or {}).get("mode", "sandbox"),
             "- Status: " + room["status"] + ", verdict " + str(room.get("verdict")) + ", fix rounds " + str(room.get("fixes", 0)) + " of " + str(room.get("max_fixes", "–")),
             "- Updated: " + stamp(room.get("updated_at")), "", "## Versions", ""]
    lines += ["- " + key + ": " + value for key, value in versions.items()]
    lines += ["", "## Agent turns", ""] + (["- " + item["stage"] + " on " + name(item["target"]) + " (`" + item["task_id"] + "`)" for item in stages] or ["- none"])
    publication = room.get("publication") or {}
    lines += ["", "## Delivery", ""]
    if publication.get("branch"):
        lines += ["- Branch `" + publication["branch"] + "` in " + str(publication.get("path")), "- Pull request: " + str(publication.get("pull_request") or "none"),
                  "- Review posted as: " + (", ".join(publication.get("review_posts", {}).values()) or "not posted"), "- Merge: " + str(publication.get("merge") or "not merged")]
        lines += ["- Delivery error: " + publication["error"]] if publication.get("error") else []
    else:
        lines += ["- Kept in AABridge; nothing was placed in the project."]
    lines += ["", "## Tokens and activity", ""]
    totals = usage_totals(room)
    for role in ("leader", "follower"):
        used = totals.get(role)
        lines.append("- " + ("Leader" if role == "leader" else "Worker") + ": " + (f"{used['input']} in, {used['output']} out, {used['cached']} cached" if used else "no token counts reported")
                     + f"; {activity.get(role, 0)} recorded actions")
    lines += ["", "## Timeline", ""]
    for entry in room["messages"]:
        text = " ".join(entry["text"].split())
        lines.append("- " + stamp(entry["time"])[11:] + " **" + entry["speaker"] + "**: " + text[:200] + ("…" if len(text) > 200 else ""))
    return "\n".join(lines) + "\n"


def main() -> int:
    from aabridge_preflight.paths import default_state_directory
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, default=default_state_directory())
    parser.add_argument("--team", help="Team id; the latest task by default.")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "evidence" / ("team-run-" + time.strftime("%Y-%m-%d-%H%M") + ".md"))
    parser.add_argument("--no-ping", action="store_true", help="Do not ask each Mac's service for its version.")
    args = parser.parse_args()
    text = record(args.state_dir, args.team, not args.no_ping)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    print(text.split("## Task")[0].strip() + "\n\nSaved to " + str(args.output))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from source_paths import source_environment
    if os.environ.get("AABRIDGE_RECORD") != "1":
        raise SystemExit(subprocess.call([sys.executable, __file__, *sys.argv[1:]], env={**source_environment(ROOT), "AABRIDGE_RECORD": "1"}))
    raise SystemExit(main())
