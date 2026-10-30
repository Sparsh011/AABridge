"""Build a universal native app from a setup directory or legacy pairing ZIP."""
from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def validate_output(output: Path, replace: bool):
    if output.suffix not in {".app", ".zip"}:
        raise ValueError("Output must end in .app or .zip.")
    if replace and output.suffix != ".app":
        raise ValueError("Only an app bundle may be replaced.")
    if output.is_symlink():
        raise ValueError("App output must not be a symbolic link.")
    if output.exists():
        if not replace:
            raise ValueError("Choose a new output filename or use --replace for an existing AABridge.app.")
        try:
            bundle = plistlib.loads((output / "Contents/Info.plist").read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError("Existing output is not an AABridge app bundle.") from exc
        if bundle.get("CFBundleIdentifier") != "local.aabridge.desktop":
            raise ValueError("Only an existing AABridge app bundle may be replaced.")


def publish_app(app: Path, output: Path, *, replace: bool):
    """Stage on the destination filesystem and restore the prior app on failure."""
    validate_output(output, replace)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aabridge-publish-", dir=output.parent) as directory:
        temporary = Path(directory)
        staged = temporary / "AABridge.app"
        shutil.copytree(app, staged)
        validate_output(output, replace)
        previous = temporary / "previous.app"
        had_previous = output.exists()
        if had_previous:
            os.replace(output, previous)
        try:
            os.replace(staged, output)
        except BaseException:
            if had_previous:
                os.replace(previous, output)
            raise


def copy_payload(args, payload: Path):
    names = set()

    def write(name, size, read):
        if name in names or size > 4_194_304:
            raise ValueError("Unexpected pairing payload layout.")
        names.add(name)
        if name not in {"Setup.command", "setup_worker.py", "manifest.json", "coordinator.pub", "runtime-paths.json", "READ-ME.txt"} and not name.endswith("-py3-none-any.whl"):
            raise ValueError("Unexpected pairing artifact.")
        (payload / name).write_bytes(read())

    if args.payload_dir:
        for item in sorted(args.payload_dir.iterdir()):
            if not item.is_file() or item.is_symlink():
                raise ValueError("Unexpected pairing payload entry.")
            write(item.name, item.stat().st_size, item.read_bytes)
    else:
        with zipfile.ZipFile(args.payload_zip) as archive:
            for item in archive.infolist():
                path = Path(item.filename)
                if len(path.parts) != 2:
                    raise ValueError("Unexpected pairing archive layout.")
                write(path.name, item.file_size, lambda: archive.read(item))


def main():
    parser = argparse.ArgumentParser()
    payload_source = parser.add_mutually_exclusive_group(required=True)
    payload_source.add_argument("--payload-zip", type=Path)
    payload_source.add_argument("--payload-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true", help="Replace an existing AABridge app after successful verification.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.absolute()
    validate_output(output, args.replace)
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
        copy_payload(args, payload)
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
        if output.suffix == ".app":
            publish_app(app, output, replace=args.replace)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(output)], check=True)
    print(json.dumps({"desktop_app" if output.suffix == ".app" else "desktop_zip": str(output), "architectures": ["arm64", "x86_64"],
                      "minimum_macos": "13.0", "model_inference_started": False}, indent=2))


if __name__ == "__main__":
    main()
