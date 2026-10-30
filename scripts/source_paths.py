"""Run repository tools against this checkout, including newly added packages."""
from __future__ import annotations

import os
from pathlib import Path
import tomllib


def source_environment(root: Path) -> dict[str, str]:
    configuration = tomllib.loads((root / "pyproject.toml").read_text())
    packages = configuration["tool"]["setuptools"]["package-dir"]
    paths = dict.fromkeys(str((root / path).parent) for path in packages.values())
    environment = dict(os.environ)
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join([*paths, *([inherited] if inherited else [])])
    return environment
