from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from aabridge_core.config import private_directory, write_private
from aabridge_protocol.messages import BridgeError, require


def sandbox_profile(runtime: Path, executable: Path, credential: Path | None = None, *, proxy_port: int | None = None,
                    probe_python_root: Path | None = None, preference_domain: str = "com.openai.codex",
                    extra_roots: tuple[str, ...] = ()) -> str:
    require(sys.platform == "darwin", "Whole-process proposal isolation is implemented only on macOS.", "isolation_unavailable")
    roots = ["/System", "/usr/lib", "/usr/share", "/usr/bin", "/bin", "/sbin", "/opt/homebrew",
             "/Library/Developer/CommandLineTools", "/Library/Apple", "/private/var/db/dyld",
             "/private/etc/ssl", "/private/etc/codex", "/Library/Keychains",
             "/Library/Managed Preferences", "/Library/Preferences"]
    # The binary and its packaged resources may be outside the standard roots.
    bundles = [parent for parent in executable.parents if parent.suffix == ".app"]
    bundle = bundles[-1] if bundles else executable.parent
    roots.extend((str(bundle), str(runtime)))
    # Adapter-specific, non-secret system data (for example the timezone database).
    for root in extra_roots:
        require(root.startswith(("/System/", "/usr/", "/private/var/db/")), "Unsafe extra sandbox read root.", "isolation_unavailable")
        roots.append(root)
    if probe_python_root is not None:
        # Only the local canary interpreter needs its Python installation. The
        # Codex worker never receives this grant. In particular, python.org and
        # Intel Homebrew framework libraries are outside /opt/homebrew.
        prefix = probe_python_root.resolve(strict=True)
        require(prefix not in (Path("/"), Path.home(), Path("/Users"), Path("/Volumes")),
                "Unsafe Python installation prefix for isolation probe.", "isolation_unavailable")
        roots.append(str(prefix))
    # Keep platform IPC/bootstrap semantics, then explicitly deny content reads,
    # writes, executable children, and network operations outside finite grants.
    # This is not full desktop/IPC isolation; the signed provider harness and OS
    # services remain trusted. It is a proposal worker, not arbitrary-code hosting.
    rules = ["(version 1)", "(allow default)"]
    readable = [f"(subpath {json.dumps(root)})" for root in roots]
    for path in ("/", "/dev/null", "/dev/random", "/dev/urandom", "/private/etc/localtime", "/private/etc/hosts", "/private/etc/resolv.conf"):
        readable.append(f"(literal {json.dumps(path)})")
    if credential:
        readable.append(f"(literal {json.dumps(str(credential))})")
    rules.append("(deny file-read-data (require-not (require-any " + " ".join(readable) + ")))")
    rules.append(f'(deny file-write* (require-not (require-any (subpath {json.dumps(str(runtime))}) (subpath "/dev/fd") (literal "/dev/null"))))')
    rules.append(f'(deny process-exec (require-not (literal {json.dumps(str(executable))})))')
    rules.append('(deny process-fork)')
    rules.append('(deny mach-priv-host-port)')
    rules.append('(deny signal (require-not (target self)))')
    rules.append('(deny network-inbound network-bind)')
    rules.append(f'(deny user-preference-read user-preference-write (require-not (preference-domain {json.dumps(preference_domain)})))')
    network = ['(literal "/private/var/run/syslog")']
    if proxy_port is not None:
        # Seatbelt cannot filter public hosts/IPs. Permit only the per-worker
        # authenticated CONNECT proxy; it independently restricts destinations.
        require(type(proxy_port) is int and 1 <= proxy_port <= 65535, "Invalid private proxy port.")
        network.append(f'(remote tcp "localhost:{proxy_port}")')
    rules.append("(deny network-outbound (require-not (require-any " + " ".join(network) + ")))")
    return "\n".join(rules) + "\n"


PROBE_CHECKS = frozenset({"outside_read_denied", "outside_write_denied", "outside_delete_denied",
                          "network_denied", "runtime_write_allowed", "child_exec_denied"})


def isolation_report(directory: Path | None = None) -> dict:
    require(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists(), "macOS process isolation is unavailable.", "isolation_unavailable")
    # These disposable canaries are not a repository, credential, or user file.
    with tempfile.TemporaryDirectory(prefix="aabridge-isolation-", dir=directory) as name:
        root = Path(name).resolve()
        runtime = private_directory(root / "runtime", create=True)
        secret = root / "outside-canary"
        secret.write_text("disposable-nonsecret-canary")
        profile = root / "probe.sb"
        # Homebrew's bin/python is a launcher that execs the framework image.
        # Probe the actual image so only one exact executable is authorized.
        interpreter = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
        if not interpreter.exists():
            interpreter = Path(sys.executable).resolve()
        interpreter = interpreter.resolve()
        prefix = Path(sys.base_prefix).resolve(strict=True)
        require(not root.is_relative_to(prefix), "Probe canaries must be outside the Python installation.", "isolation_unavailable")
        profile.write_text(sandbox_profile(runtime, interpreter, probe_python_root=prefix))
        program = '''import json, os, socket, subprocess, sys
secret, runtime = sys.argv[1:]
checks = {}
for name, action in (
    ("outside_read_denied", lambda: open(secret).read()),
    ("outside_write_denied", lambda: open(secret, "w").write("changed")),
    ("outside_delete_denied", lambda: os.unlink(secret)),
    ("network_denied", lambda: socket.create_connection(("1.1.1.1", 80), timeout=1)),
):
    try:
        action()
        checks[name] = False
    except PermissionError:
        checks[name] = True
    except OSError:
        checks[name] = False
with open(os.path.join(runtime, "allowed"), "w") as stream:
    stream.write("allowed")
checks["runtime_write_allowed"] = True
try:
    child = subprocess.run(["/bin/cat", secret], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checks["child_exec_denied"] = False
except PermissionError:
    checks["child_exec_denied"] = True
print(json.dumps(checks))
'''
        diagnostic = ""
        returncode = None
        try:
            result = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(profile), str(interpreter),
                                     "-I", "-S", "-c", program, str(secret), str(runtime)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=15, cwd=runtime, env={"PATH": "/usr/bin:/bin", "HOME": str(runtime)})
            returncode = result.returncode
            checks = json.loads(result.stdout) if result.returncode == 0 else {}
            if result.returncode != 0:
                # Never include arbitrary subprocess stderr in operator reports.
                error = result.stderr.lower()
                if b"sandbox_init" in error or b"sandbox initialization" in error:
                    diagnostic = "macOS refused to initialize the sandbox (possibly a restricted parent process)."
                elif b"encodings" in error or b"python path configuration" in error:
                    diagnostic = "The probe interpreter could not load its Python standard library."
                elif b"dyld" in error:
                    diagnostic = "macOS could not load a required probe interpreter library."
                else:
                    diagnostic = "The sandboxed probe interpreter exited before completing its checks."
        except subprocess.TimeoutExpired:
            checks = {}
            diagnostic = "The sandbox probe timed out."
        except (OSError, ValueError):
            checks = {}
            diagnostic = "The sandbox probe could not start or returned an invalid report."
        if not isinstance(checks, dict):
            checks = {}
        valid = set(checks) == PROBE_CHECKS and all(value is True for value in checks.values())
        unchanged = secret.exists() and secret.read_text() == "disposable-nonsecret-canary"
        passed = valid and unchanged
        failed = sorted(key for key in PROBE_CHECKS if checks.get(key) is not True)
        if not unchanged:
            failed.append("canary_preserved")
        if set(checks) - PROBE_CHECKS:
            failed.append("unexpected_report_fields")
        return {"profile": "macos-proposal-v1", "passed": passed, "checks": checks,
                "failed_checks": failed, "diagnostic": diagnostic, "probe_exit_code": returncode,
                "python_version": sys.version.split()[0], "python_prefix": str(prefix),
                "limits": ["Trusted Codex harness must read its local provider credential.", "Live provider connectivity and tool configuration are checked separately."]}


def probe_isolation(directory: Path | None = None) -> dict:
    report = isolation_report(directory)
    detail = report["diagnostic"] or "Failed checks: " + ", ".join(report["failed_checks"])
    require(report["passed"], "Whole-process isolation probes did not pass; worker execution stays disabled. " +
            detail + " Python " + report["python_version"] + " at " + report["python_prefix"], "isolation_unavailable")
    return report
