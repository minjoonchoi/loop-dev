"""Atomic state, repository snapshots, and process ownership (POSIX)."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from datetime import datetime, timezone


HARNESS_SKILL_FILES = (
    ".agents/skills/loop-work/SKILL.md",
    ".agents/skills/loop-work/agents/openai.yaml",
    ".claude/skills/loop-work/SKILL.md",
)


class LoopError(Exception):
    """An actionable, non-interactive failure."""


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LoopError(f"Cannot read JSON {path}: {exc}") from exc


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".loop-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inside(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise LoopError(f"Expected a repository-relative path: {relative!r}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise LoopError(f"Path escapes repository: {relative}")
    return path


def git(root, *args, required=True):
    result = subprocess.run(["git", "-C", str(root), *args], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=30)
    if required and result.returncode:
        raise LoopError(result.stderr.decode(errors="replace").strip() or "Git failed")
    return result.stdout


def repository(path):
    root = Path(path).resolve()
    actual = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != actual:
        raise LoopError(f"Use the repository root: {actual}")
    return root


def head(root):
    return git(root, "rev-parse", "--verify", "HEAD", required=False).decode().strip() or "UNBORN"


def files(root):
    raw = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted(set(os.fsdecode(item) for item in raw.split(b"\0") if item))


def file_hash(path):
    if path.is_symlink():
        return digest({"link": os.readlink(path)})
    if not path.exists():
        return "deleted"
    if not path.is_file():
        raise LoopError(f"Unsupported repository entry (submodules need separate goals): {path}")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return digest({"content": result.hexdigest(), "executable": bool(path.stat().st_mode & 0o111)})


def snapshot(root):
    # Include staged, unstaged, deleted and untracked files, but never our own logs.
    entries = {name: file_hash(root / name) for name in files(root)
               if not name.startswith(".loop/state/")}
    return {"head": head(root), "files": entries, "digest": digest([head(root), entries])}


def rule_manifest(root, declared, missing_ok=False):
    names = set(declared)
    names.update(name for name in files(root)
                 if Path(name).name in ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
                 or name.startswith(".loop/rules/"))
    for name in (".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml", *HARNESS_SKILL_FILES):
        if (root / name).exists():
            names.add(name)
    result = {}
    for name in sorted(names):
        path = inside(root, name)
        if not path.is_file() and not missing_ok:
            raise LoopError(f"Rule file does not exist: {name}")
        result[name] = file_hash(path)
    return result


def harness_digest():
    """Seal the running source policy, independently of target repository paths.

    Only package code, built-in Markdown rules and canonical skill metadata are
    read. Installed wrappers may link this source outside the target; those
    paths must never become target rule/read permissions.
    """
    package = Path(__file__).resolve().parent
    source = package.parent
    paths = list(package.glob("*.py")) + list((source / "rules").glob("*.md"))
    paths += [source / "skills/loop-work/SKILL.md",
              source / "skills/loop-work/agents/openai.yaml"]
    return digest({"version": 1, "files": {
        path.relative_to(source).as_posix(): file_hash(path) for path in sorted(paths)}})


def engine_digest():
    return harness_digest()


@contextlib.contextmanager
def locked(root):
    path = root / ".loop/state/lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LoopError("Another loop command is running in this repository") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
