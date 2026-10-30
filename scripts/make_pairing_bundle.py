"""Build a reviewable setup directory or ZIP with a wheel and public key only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--peer", default="macbook")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--directory", action="store_true", help="Write files directly instead of a ZIP.")
    parser.add_argument("--wheel", type=Path, help="Use a wheel built in a temporary directory.")
    parser.add_argument("--codex-path", help="Local worker app/executable path, including an external drive; never a peer-supplied command.")
    parser.add_argument("--claude-path", help="Optional independent local Claude executable path.")
    args = parser.parse_args()
    from aabridge_protocol.messages import identifier
    identifier(args.peer)
    from aabridge_preflight.runtimes import local_path
    selections = {"version": 1}
    for agent in ("codex", "claude"):
        selected = getattr(args, agent + "_path")
        if selected is not None:
            local_path(selected)  # worker path may intentionally not exist here
            selections[agent] = selected
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    wheel = args.wheel or root / "dist" / f"aabridge-{version}-py3-none-any.whl"
    if wheel.name != f"aabridge-{version}-py3-none-any.whl":
        raise SystemExit("Build the wheel for the current source version first.")
    public = args.public_key.read_bytes()
    parts = public.decode().strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or len(public) > 4096:
        raise SystemExit("Supply only a dedicated Ed25519 public .pub key, never a private key.")
    fingerprint = subprocess.check_output(["/usr/bin/ssh-keygen", "-lf", str(args.public_key)], text=True).strip()
    if not wheel.is_file():
        raise SystemExit("Build the current wheel first.")
    if args.output.exists() or args.output.is_symlink():
        raise SystemExit("Output already exists; choose a new filename. Nothing overwritten.")
    with tempfile.TemporaryDirectory(prefix="aabridge-bundle-") as temporary:
        folder = Path(temporary) / args.output.stem
        folder.mkdir()
        shutil.copy2(wheel, folder / wheel.name)
        for name in ("Setup.command", "setup_worker.py"):
            shutil.copy2(root / "apps/installer" / name, folder / name)
        (folder / "Setup.command").chmod(0o755)
        (folder / "coordinator.pub").write_bytes(public)
        (folder / "runtime-paths.json").write_text(json.dumps(selections, indent=2) + "\n")
        (folder / "manifest.json").write_text(json.dumps({"version": 1, "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "public_key_sha256": hashlib.sha256(public).hexdigest(), "peer_id": args.peer}, indent=2) + "\n")
        (folder / "READ-ME.txt").write_text(
            "On the Mac mini: extract this folder, then double-click Setup.command.\n"
            "Run it under the account allowed by Remote Login, with Codex installed/signed in.\n"
            "runtime-paths.json selects existing app/executable locations on THIS worker.\n"
            "Codex can live on an external SSD; Claude is discovered independently locally.\n"
            "Edit a path if needed, keeping it absolute (macOS mounted drives use /Volumes).\n"
            "Keep the selected SSD connected; an unavailable selected path never falls back.\n"
            "Claude Code workers are enabled per project later (see docs/claude-worker.md).\n"
            "This installs a private Python environment and the bundled bridge, creates a disposable\n"
            "demo repository, checks Codex without inference, starts the local supervisor and\n"
            "appends a restricted SSH entry for only that demo project. Existing key lines remain.\n"
            "If Python 3.11+ is missing, the wrapper uses existing Homebrew to install Python 3.11.\n"
            "No Homebrew bootstrap, sudo, model turn, API fallback or provider login is performed.\n"
            "The only included SSH key is PUBLIC; no password or private key is included.\n"
            "Expected coordinator public fingerprint: " + fingerprint + "\n"
            "Wait for AABRIDGE MINI READY, then tell the MacBook chat.\n"
            "If setup fails, paste only its final SETUP INCOMPLETE line, not raw provider logs.\n"
            "Inspect Setup.command and setup_worker.py before running if desired.\n")
        if args.directory:
            shutil.copytree(folder, args.output)
        else:
            with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(folder.iterdir()):
                    archive.write(item, arcname=str(Path(folder.name) / item.name))
    print(json.dumps({"bundle": str(args.output.resolve()), "public_key_fingerprint": fingerprint,
                      "contains_private_key": False, "model_inference_started": False}, indent=2))


if __name__ == "__main__":
    main()
