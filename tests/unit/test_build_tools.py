"""Regression checks for pulled source imports and replacing the local app."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop = script("build_desktop")
sources = script("source_paths")


class BuildToolsTests(unittest.TestCase):
    def test_source_packages_import_without_any_editable_install(self):
        packages = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-dir"]
        # -S disables site-packages, including setuptools' editable module finder.
        # This reproduces a missing/stale finder after a pull adds a new package.
        probe = "import importlib.util, json; print(json.dumps({name: importlib.util.find_spec(name).origin for name in " + repr(list(packages)) + "}))"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-S", "-c", probe], cwd=directory,
                                    env=sources.source_environment(ROOT), capture_output=True, text=True, check=True)
        import json
        for name, location in json.loads(result.stdout).items():
            self.assertEqual(Path(location), ROOT / packages[name] / "__init__.py")

    def app(self, directory, name, text):
        app = Path(directory) / name
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "local.aabridge.desktop"}))
        (app / "Contents/version").write_text(text)
        return app

    def test_publish_replaces_single_app_without_leaving_output_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.app(directory, "source/AABridge.app", "new")
            output = self.app(directory, "output/AABridge.app", "old")
            desktop.publish_app(source, output, replace=True)
            self.assertEqual((output / "Contents/version").read_text(), "new")
            self.assertEqual(list(output.parent.iterdir()), [output])
            # Repeating a build uses the same destination and leaves no backup.
            desktop.publish_app(source, output, replace=True)
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_failed_publication_restores_existing_app(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.app(directory, "source/AABridge.app", "new")
            output = self.app(directory, "output/AABridge.app", "old")
            replace = os.replace

            def fail_install(src, dst):
                if Path(src).name == "AABridge.app" and Path(src).parent.name.startswith(".aabridge-publish-"):
                    raise OSError("Simulated publication failure")
                return replace(src, dst)

            with patch.object(desktop.os, "replace", side_effect=fail_install), self.assertRaises(OSError):
                desktop.publish_app(source, output, replace=True)
            self.assertEqual((output / "Contents/version").read_text(), "old")
            self.assertEqual(list(output.parent.iterdir()), [output])

    def test_unrelated_or_symlinked_outputs_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.app(directory, "source/AABridge.app", "new")
            output = Path(directory) / "other.app"
            output.mkdir()
            canary = output / "keep.txt"
            canary.write_text("preserve")
            with self.assertRaises(ValueError):
                desktop.publish_app(source, output, replace=True)
            alias = Path(directory) / "alias.app"
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaises(ValueError):
                desktop.publish_app(source, alias, replace=True)
            self.assertEqual(canary.read_text(), "preserve")
            self.assertTrue(alias.is_symlink())
