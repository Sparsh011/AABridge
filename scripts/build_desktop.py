"""Build a native macOS receiver GUI around an existing public-key pairing ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Choose a new output filename; existing artifacts are preserved.")
    with tempfile.TemporaryDirectory(prefix="aabridge-desktop-build-") as temporary:
        work = Path(temporary)
        app = work / "AABridge.app"
        contents = app / "Contents"
        binary = contents / "MacOS/AABridge"
        binary.parent.mkdir(parents=True)
        payload = contents / "Resources/SetupPayload"
        payload.mkdir(parents=True)
        for name in ("activity_backend.py", "Activity.command"):
            (contents / "Resources" / name).write_bytes((root / "apps/desktop" / name).read_bytes())
        with zipfile.ZipFile(args.payload_zip) as archive:
            names = set()
            for item in archive.infolist():
                path = Path(item.filename)
                if len(path.parts) != 2 or path.name in names or item.file_size > 4_194_304:
                    raise SystemExit("Unexpected pairing archive layout.")
                names.add(path.name)
                if path.name not in {"Setup.command", "setup_worker.py", "manifest.json", "coordinator.pub", "runtime-paths.json", "READ-ME.txt"} and not path.name.endswith("-py3-none-any.whl"):
                    raise SystemExit("Unexpected pairing artifact.")
                (payload / path.name).write_bytes(archive.read(item))
        manifest = json.loads((payload / "manifest.json").read_text())
        wheel_name = manifest["wheel"]
        if Path(wheel_name).name != wheel_name:
            raise SystemExit("Invalid wheel name.")
        for name, expected in ((wheel_name, manifest["wheel_sha256"]), ("coordinator.pub", manifest["public_key_sha256"])):
            if hashlib.sha256((payload / name).read_bytes()).hexdigest() != expected:
                raise SystemExit("Pairing artifact checksum mismatch.")
        release = wheel_name.split("-")[1]
        build = release.rsplit("dev", 1)[-1] if "dev" in release else "1"
        plist = {"CFBundleExecutable": "AABridge", "CFBundleIdentifier": "local.aabridge.desktop",
                 "CFBundleName": "AABridge", "CFBundleDisplayName": "AABridge",
                 "CFBundlePackageType": "APPL", "CFBundleShortVersionString": release.split(".dev")[0],
                 "CFBundleVersion": build, "LSMinimumSystemVersion": "13.0",
                 "NSHighResolutionCapable": True}
        (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
        # Compile one source snapshot so concurrent agent edits cannot change a
        # compiler input halfway through the universal build.
        sources = work / "Sources"
        sources.mkdir()
        for source in sorted((root / "apps/desktop").glob("*.swift")):
            (sources / source.name).write_bytes(source.read_bytes())
        images = []
        for arch in ("arm64", "x86_64"):
            image = work / ("AABridge-" + arch)
            subprocess.run(["/usr/bin/xcrun", "swiftc", "-swift-version", "5", "-parse-as-library", "-O",
                            "-target", arch + "-apple-macosx13.0", "-module-cache-path", str(work / "modules"),
                            "-framework", "SwiftUI", "-framework", "AppKit",
                            *map(str, sorted(sources.glob("*.swift"))), "-o", str(image)], check=True)
            images.append(str(image))
        subprocess.run(["/usr/bin/lipo", "-create", *images, "-output", str(binary)], check=True)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
        subprocess.run([str(binary), "--smoke-test"], check=True)
        subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(output)], check=True)
    print(json.dumps({"desktop_zip": str(output), "architectures": ["arm64", "x86_64"],
                      "minimum_macos": "13.0", "model_inference_started": False}, indent=2))


if __name__ == "__main__":
    main()
