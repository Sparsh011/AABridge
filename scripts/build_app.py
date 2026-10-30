"""Build one replaceable app; wheel and setup intermediates are temporary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from source_paths import source_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-path", type=Path, default=Path("build/AABridge.app"))
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--peer", default="macbook")
    parser.add_argument("--codex-path", default="")
    parser.add_argument("--claude-path", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.public_key.is_file():
        raise SystemExit('Pairing public key missing. Use make build PUBLIC_KEY="/absolute/path/to/key.pub" with an existing dedicated Ed25519 public key.')
    environment = source_environment(root)

    def run(*arguments):
        subprocess.run([sys.executable, *map(str, arguments)], cwd=root, env=environment, check=True)

    selections = root / 'build/runtime-paths.json'
    run('scripts/select_build_runtimes.py', '--output', selections,
        '--codex-path', args.codex_path, '--claude-path', args.claude_path)
    paths = json.loads(selections.read_text())
    runtime_arguments = [argument for agent in ('codex', 'claude') if paths.get(agent)
                         for argument in ('--' + agent + '-path', paths[agent])]
    with tempfile.TemporaryDirectory(prefix="aabridge-app-build-") as directory:
        temporary = Path(directory)
        wheels = temporary / "wheels"
        run("-m", "build", "--wheel", "--no-isolation", "--outdir", wheels)
        wheel, = wheels.glob("*.whl")
        payload = temporary / "SetupPayload"
        run("scripts/make_pairing_bundle.py", "--public-key", args.public_key,
            "--peer", args.peer, "--wheel", wheel, "--directory", "--output", payload, *runtime_arguments)
        run("scripts/build_desktop.py", "--payload-dir", payload,
            "--output", args.app_path.absolute(), "--replace")
    print(f"Built app: {args.app_path.absolute()} (replaced in place; no ZIPs)")


if __name__ == "__main__":
    main()
