from __future__ import annotations

import ast
import operator
import os
import stat
import subprocess
from pathlib import Path

from aabridge_core.config import private_directory
from aabridge_protocol.messages import BridgeError, fields, integer, relative_file, require, text

MAX_SOURCE_BYTES = 16384


def git(repository: Path, *arguments: str) -> str:
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(repository), "LANG": "C.UTF-8",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_COUNT": "0", "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    command = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "commit.gpgSign=false", "-c", "user.name=AABridge Worker",
               "-c", "user.email=worker@localhost", "-C", str(repository), *arguments]
    try:
        process = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise BridgeError("git_failed", "Configured local Git operation failed or timed out.") from None
    require(process.returncode == 0, "Configured local Git operation failed; inspect the preserved task workspace.", "git_failed")
    require(len(process.stdout) <= 2_000_000, "Repository metadata exceeds the supported size.")
    return process.stdout.decode("utf-8", errors="strict").strip()


def validate_verifier(value: object) -> dict:
    if isinstance(value, dict) and value.get("profile") == "repository-v1":
        return fields(value, {"profile"})
    if isinstance(value, dict) and value.get("profile") == "file-proposal-v1":
        profile = fields(value, {"profile", "files"})
        require(isinstance(profile["files"], list) and 1 <= len(profile["files"]) <= 16, "Choose 1–16 explicit file paths.")
        require(all(isinstance(path, str) for path in profile["files"]), "Scope paths must be text.")
        require(len(set(profile["files"])) == len(profile["files"]), "Scope paths must be unique.")
        for path in profile["files"]:
            relative_file(path)
        return profile
    profile = fields(value, {"profile", "file", "function", "cases"})
    require(profile["profile"] == "integer-function-v1", "Unsupported verification profile; executable repository scripts are disabled.", "permission_denied")
    relative_file(profile["file"])
    require(profile["file"].endswith(".py"), "Integer-function verification requires a Python source file.")
    require(isinstance(profile["function"], str) and profile["function"].isidentifier() and not profile["function"].startswith("_"), "Invalid function name.")
    require(isinstance(profile["cases"], list) and 1 <= len(profile["cases"]) <= 32, "Expected 1-32 verification cases.")
    for case in profile["cases"]:
        fields(case, {"args", "expected"})
        require(isinstance(case["args"], list) and 1 <= len(case["args"]) <= 4, "Expected 1-4 integer arguments.")
        for argument in case["args"]:
            integer(argument, -1000000, 1000000)
        integer(case["expected"], -10**12, 10**12)
    return profile


def verify_source(source: str, profile: dict) -> dict:
    """Interpret a tiny arithmetic AST; never compile/exec/import repository code."""
    require(len(source.encode()) <= MAX_SOURCE_BYTES, "Source exceeds the profile size limit.")
    try:
        tree = ast.parse(source)
        require(len(list(ast.walk(tree))) <= 128, "AST exceeds the profile size limit.")
        require(len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef), "Profile permits one function and no module-level executable statements.")
        function = tree.body[0]
        require(function.name == profile["function"] and not function.decorator_list and function.returns is None, "Unexpected function definition.")
        args = function.args
        require(len({argument.arg for argument in args.args}) == len(args.args), "Duplicate function parameter names are invalid.")
        require(not args.vararg and not args.kwarg and not args.kwonlyargs and not args.defaults and not args.kw_defaults and not args.posonlyargs, "Unsupported function parameters.")
        require(all(argument.annotation is None for argument in args.args), "Annotations are not part of this profile.")
        body = function.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        require(len(body) == 1 and isinstance(body[0], ast.Return), "Profile permits only a return expression.")
        operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                      ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
        def evaluate(node, bindings):
            if isinstance(node, ast.Constant) and type(node.value) is int:
                result = node.value
            elif isinstance(node, ast.Name) and node.id in bindings:
                result = bindings[node.id]
            elif isinstance(node, ast.BinOp) and type(node.op) in operations:
                result = operations[type(node.op)](evaluate(node.left, bindings), evaluate(node.right, bindings))
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                result = evaluate(node.operand, bindings) * (-1 if isinstance(node.op, ast.USub) else 1)
            else:
                raise BridgeError("verification_failed", "Unsupported AST expression; no repository code was executed.")
            require(abs(result) <= 10**12, "Arithmetic exceeds the profile bounds.")
            return result
        outcomes = []
        for case in profile["cases"]:
            require(len(case["args"]) == len(args.args), "Argument count does not match the verification profile.")
            actual = evaluate(body[0].value, dict(zip((arg.arg for arg in args.args), case["args"])))
            require(actual == case["expected"], f"Demo verification failed: {profile['function']}({', '.join(map(str, case['args']))}) returned {actual}; expected {case['expected']}. This project uses fixed integer-function tests. Choose a file-proposal project for other work. No result commit was accepted.", "verification_failed")
            outcomes.append({"args": case["args"], "expected": case["expected"], "actual": actual, "exit_code": 0})
        return {"profile": "integer-function-v1", "file": profile["file"], "method": "restricted AST interpretation; no shell/exec/import", "exit_code": 0, "cases": outcomes}
    except (SyntaxError, ValueError, RecursionError, ZeroDivisionError):
        raise BridgeError("verification_failed", "Source could not be verified by the restricted AST profile.") from None


def safe_source(root: Path, relative: str) -> tuple[Path, str]:
    relative_file(relative)
    path = root / relative
    current = root
    for component in relative.split("/"):
        current = current / component
        require(not current.is_symlink(), "Source contains a symlink.", "unsafe_workspace")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Source is not an independent regular file.")
        data = stream.read(MAX_SOURCE_BYTES + 1)
        require(len(data) <= MAX_SOURCE_BYTES, "Source exceeds the context limit.")
    return path, data.decode("utf-8")


def scoped_source(root: Path, relative: str, *, allow_new: bool = False) -> tuple[Path, str | None]:
    """Read an existing regular file, or validate an explicitly approved new path."""
    relative_file(relative)
    current = root
    for component in relative.split("/"):
        current = current / component
        require(not current.is_symlink(), "Source contains a symlink.", "unsafe_workspace")
        if current.exists() and current != root / relative:
            require(current.is_dir(), "A scope parent is not a directory.", "unsafe_workspace")
    path = root / relative
    if allow_new and not path.exists():
        return path, None
    return safe_source(root, relative)


class Workspace:
    def __init__(self, state: Path, task: dict, project: dict):
        self.task, self.project = task, project
        self.path = state / "workspaces" / task["id"]

    @property
    def branch(self) -> str:
        # Name the task branch after the agent that does the work.
        return ("claude" if self.project.get("adapter") == "claude" else "codex") + "/" + self.task["id"]

    def prepare(self) -> dict[str, str]:
        if not self.path.exists():
            source = Path(self.project["repository"])
            require(source.is_dir() and source.resolve() == source, "Registered repository path is no longer canonical.", "unsafe_workspace")
            actual = git(source, "rev-parse", "--verify", "--end-of-options", self.task["base_commit"] + "^{commit}")
            require(actual == self.task["base_commit"], "Exact base commit is unavailable locally.", "base_unavailable")
            # No local hardlinks, templates, user filters, hooks, or submodules.
            git(self.path.parent, "clone", "--no-local", "--no-hardlinks", "--no-checkout", "--template=", "--", str(source), str(self.path))
            entries = git(self.path, "ls-tree", "-r", "--full-tree", self.task["base_commit"]).splitlines()
            require(self.project["verification"]["profile"] == "repository-v1" or (len(entries) <= 5000 and all(entry.split(" ", 1)[0] in {"100644", "100755"} for entry in entries)), "Symlinks, submodules, and oversized repositories are unsupported.", "unsafe_workspace")
            git(self.path, "checkout", "-b", self.branch, self.task["base_commit"])
            # A task may never accidentally push back into the source checkout.
            git(self.path, "remote", "remove", "origin")
        require(not self.path.is_symlink(), "Task workspace was replaced by a symlink.", "unsafe_workspace")
        if self.project.get("links"):
            from . import linked
            linked.prepare(self.path.parent.parent, self.task, self.project, git(self.path, "branch", "--show-current"))
        private_directory(self.path.parent)
        if self.project["verification"]["profile"] == "repository-v1":
            return {}  # Native CLIs read the checkout directly, without a context-size cap.
        sources = {path: scoped_source(self.path, path, allow_new=self.project["verification"]["profile"] == "file-proposal-v1")[1] for path in self.task["scope"]}
        require(sum(len((source or "").encode()) for source in sources.values()) <= 65536, "Selected source context exceeds 64 KiB; choose fewer files.")
        return sources

    def result(self, proposal: dict) -> dict:
        if self.project["verification"]["profile"] == "repository-v1":
            return self.coding_result(proposal)
        edits = proposal["edits"]
        require(len(edits) <= len(self.task["scope"]), "Proposal exceeds file scope.", "permission_denied")
        proposed = {}
        for edit in edits:
            fields(edit, {"path", "content"})
            path = relative_file(edit["path"])
            require(path in self.task["scope"] and path not in proposed, "Unauthorized or duplicated proposed file.", "permission_denied")
            proposed[path] = text(edit["content"], MAX_SOURCE_BYTES, empty=True)
            require(len(proposed[path].encode()) <= MAX_SOURCE_BYTES, "Proposed file exceeds the 16 KiB limit.")
        verifier = self.project["verification"]
        file_proposal = verifier["profile"] == "file-proposal-v1"
        if file_proposal:
            require(set(self.task["scope"]) <= set(verifier["files"]), "Task scope exceeds the approved file profile.", "permission_denied")
            require(sum(len(content.encode()) for content in proposed.values()) <= 65536, "Proposed contents exceed 64 KiB.")
            for relative in proposed:
                scoped_source(self.path, relative, allow_new=True)
            evidence = {"profile": "file-proposal-v1", "files": self.task["scope"], "method": "File scope and Git diff checked; application code and tests were not executed.", "exit_code": 0, "review_required": True}
        else:
            require(set(self.task["scope"]) == {verifier["file"]}, "Every writable file must be covered by the supported verification profile.", "permission_denied")
            _, current = safe_source(self.path, verifier["file"])
            evidence = verify_source(proposed.get(verifier["file"], current), verifier)
        # All data validated before writing. No model-provided patches or commands.
        for relative, content in proposed.items():
            path, previous = scoped_source(self.path, relative, allow_new=file_proposal)
            if previous is None:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                scoped_source(self.path, relative, allow_new=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if previous is None else 0), 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe proposed edit target.")
                os.ftruncate(stream.fileno(), 0)
                stream.write(content.encode())
                stream.flush()
                os.fsync(stream.fileno())
        stage_paths = [path for path in self.task["scope"] if (self.path / path).exists()]
        if stage_paths:
            git(self.path, "add", "--", *stage_paths)
        dirty = git(self.path, "diff", "--cached", "--name-only")
        if dirty:
            git(self.path, "commit", "-m", f"AABridge {self.task['id']} revision {self.task['revision']}")
        result_commit = git(self.path, "rev-parse", "HEAD")
        changed = git(self.path, "diff", "--name-only", self.task["base_commit"], result_commit).splitlines()
        require(set(changed) <= set(self.task["scope"]), "Independent diff inspection found an out-of-scope change.", "permission_denied")
        require(not git(self.path, "status", "--porcelain"), "Task checkout contains uncommitted changes.", "verification_failed")
        if not file_proposal:
            actual = safe_source(self.path, verifier["file"])[1]
            verify_source(actual, verifier)
        return {"base_commit": self.task["base_commit"], "result_commit": result_commit,
                "branch": "detached HEAD", "worktree": str(self.path), "changed_paths": changed,
                "summary": proposal["summary"], "verification": [evidence], "issues": proposal["issues"],
                "limitations": ["Proposal-only worker; no arbitrary shell or repository test scripts executed.", "Not pushed or merged."] + (["Review required: scope validation does not prove correctness. New files exist in the task checkout, not the original project folder."] if file_proposal else [])}


    def coding_result(self, proposal: dict) -> dict:
        branch = git(self.path, "branch", "--show-current")
        # Workspaces created before agent-named branches keep their codex/ name.
        require(branch in {self.branch, "codex/" + self.task["id"]},
                "The agent changed its assigned task branch; checkout preserved for review.", "verification_failed")
        # Native agents can add/delete files and execute project checks. Keep all
        # accepted changes in this isolated clone, with no push remote configured.
        if self.task.get("mode", "work") != "work":
            require(not git(self.path, "status", "--porcelain") and
                    git(self.path, "rev-parse", "HEAD") == self.task["base_commit"],
                    "Planning/review must leave the checkout unchanged.", "permission_denied")
        else:
            git(self.path, "add", "--all")
            if git(self.path, "diff", "--cached", "--name-only"):
                git(self.path, "commit", "-m", f"AABridge {self.task['id']} revision {self.task['revision']}")
        commit = git(self.path, "rev-parse", "HEAD")
        from . import linked
        links, notes = linked.results(self.path.parent.parent, self.task, self.project, branch, self.task.get("mode", "work") == "work")
        return {"base_commit": self.task["base_commit"], "result_commit": commit,
                **({"linked": links} if links else {}),
                "branch": branch, "worktree": str(self.path),
                "changed_paths": git(self.path, "diff", "--name-only", self.task["base_commit"], commit).splitlines(),
                "summary": proposal["summary"], "issues": proposal.get("issues", []),
                "verification": [{"profile": "repository-v1", "review_required": True,
                                  "method": "Isolated Git branch captured. Agent-reported checks require review."}],
                "limitations": ["Not pushed or merged.", "Native CLI commands use the permissions of this Mac's signed-in user.", *notes]}
