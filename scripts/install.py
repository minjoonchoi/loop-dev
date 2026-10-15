#!/usr/bin/env python3
"""Install the same rules and gates into a target repository without replacing its rules."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))

from loop_work.storage import LoopError, file_hash, inside, locked, read_json, write_json, write_text
from loop_work.knowledge import project_root


START = "<!-- loop-work:start -->"
END = "<!-- loop-work:end -->"
IGNORE_START = "# loop-work:ignore:start"
IGNORE_END = "# loop-work:ignore:end"
IGNORE_BLOCK = f"""{IGNORE_START}
/.loop/install/
/.loop/loop.py
/.agents/skills/loop-work
/.claude/skills/loop-work
/.loop/state/
{IGNORE_END}
"""
BLOCK = f"""{START}
## loop-work conversation binding

loop-work activates only on explicit skill invocation. Once enabled,
route EVERY subsequent input through the bound session until explicit off, across goals, task types,
directories and compaction. Follow the installed loop-work SKILL.md; preserve its
absolute entrypoint, knowledge home and session ID. After user input, worker result
or compaction, read `orchestration context` for that binding, then only references
needed for the next action. Explicitly assigned workers/reviewers execute only their supplied
assignment; never activate or orchestrate. If the entrypoint is unavailable, stop
routing and report it; never fall back or silently reactivate.
{END}
"""


def managed_files():
    result = {".loop/loop.py": (SOURCE / "scripts/loop.py").read_bytes()}
    for folder in ("loop_work", "rules", "templates"):
        for source in sorted((SOURCE / folder).glob("*")):
            if source.is_file() and source.suffix in (".py", ".md", ".json"):
                result[f".loop/{folder}/{source.name}"] = source.read_bytes()
    skill = (SOURCE / "skills/loop-work/SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n"):
        raise LoopError("The loop-work skill needs YAML frontmatter")
    result[".agents/skills/loop-work/SKILL.md"] = skill.encode()
    result[".agents/skills/loop-work/agents/openai.yaml"] = (SOURCE / "skills/loop-work/agents/openai.yaml").read_bytes()
    helper = SOURCE / "skills/loop-work/loop.py"
    if helper.is_file():
        for provider in (".agents", ".claude"):
            result[f"{provider}/skills/loop-work/loop.py"] = helper.read_bytes()
    # Keep one shared body; only Claude's explicit-invocation metadata differs.
    result[".claude/skills/loop-work/SKILL.md"] = skill.replace(
        "---\n", "---\ndisable-model-invocation: true\n", 1).encode()
    return result


def destination(root, name):
    path = inside(root, name)
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise LoopError(f"Refusing a symlinked managed path: {name}")
        if ancestor != path and ancestor.exists() and not ancestor.is_dir():
            raise LoopError(f"Managed parent is not a directory: {name}")
    if path.exists() and not path.is_file():
        raise LoopError(f"Managed destination is not a file: {name}")
    return path


def install(target):
    root = project_root(target)
    if root == SOURCE:
        raise LoopError("Use scripts/loop.py in the source repository; install into a separate project")
    with locked(root):
        _reject_legacy_install(root, "project")
        if (root / ".loop/state/active.json").exists():
            raise LoopError("Cannot replace the harness during an active goal")
        for state in (root / ".loop/state").glob("*/state.json"):
            if read_json(state).get("phase") == "ready":
                raise LoopError("Cannot replace the harness while a goal is frozen")
        manifest_path = destination(root, ".loop/install.json")
        old = read_json(manifest_path) if manifest_path.exists() else {"files": {}}
        managed = managed_files()
        writes = {}
        for name, content in managed.items():
            path = destination(root, name)
            if path.exists():
                previous = old["files"].get(name)
                if (previous is None and path.read_bytes() != content
                        or previous is not None and file_hash(path) != previous):
                    raise LoopError(f"Local changes in managed file; preserve/reconcile them before install: {name}")
            writes[path] = content
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = destination(root, name)
            content = path.read_bytes().decode("utf-8") if path.exists() else ""
            if START in content or END in content:
                if content.count(START) != 1 or content.count(END) != 1 or content.index(START) > content.index(END):
                    raise LoopError(f"Malformed managed block in {name}")
                start, end = content.index(START), content.index(END) + len(END)
                existing = content[start:end]
                expected = old.get("entry_block", BLOCK).strip()
                if existing.strip() != expected:
                    raise LoopError(f"Local edits in {name}'s loop-work block; reconcile before install")
                content = content[:start] + BLOCK.rstrip() + content[end:]
            else:
                content = content.rstrip() + ("\n\n" if content.strip() else "") + BLOCK
            writes[path] = content.encode()
        ignore = destination(root, ".gitignore")
        content = ignore.read_bytes().decode("utf-8") if ignore.exists() else ""
        for line in ("/.loop/state/", "/.loop/loop_work/__pycache__/"):
            if line not in content.splitlines():
                content = content.rstrip() + ("\n" if content else "") + line + "\n"
        writes[ignore] = content.encode()
        # Preflight all destination paths and edits before the first managed write.
        for path, data in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        write_json(manifest_path, {"version": 1, "entry_block": BLOCK,
                                  "files": {name: file_hash(root / name) for name in managed}})
    return root


def _present(path):
    return path.exists() or path.is_symlink()


def _write_preserving_mode(path, content):
    mode = path.stat().st_mode & 0o7777 if path.is_file() else None
    write_text(path, content)
    if mode is not None:
        path.chmod(mode)


@contextmanager
def _installation_lock(root):
    # Lock the existing scope directory: no global hook, settings or lock file.
    fd = os.open(root, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LoopError("Another loop-work installation operation is running in this scope") from exc
        yield
    finally:
        os.close(fd)


def _safe_path(root, name):
    """Check lexical destinations without following the links we intend to manage."""
    if not isinstance(name, str) or not name:
        raise LoopError(f"Invalid managed path: {name!r}")
    relative = Path(name)
    if relative == Path(".") or str(relative) != name or relative.is_absolute() or ".." in relative.parts:
        raise LoopError(f"Invalid managed path: {name}")
    path = root / relative
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise LoopError(f"Conflicting managed parent: {parent}")
    return path


def _link_layout(root, scope, source):
    install_name = ".loop/install" if scope == "project" else ".local/share/loop-work/install"
    base = root / install_name
    wrapper = '''#!/usr/bin/env python3
"""Managed loop-work entrypoint. Never fall back to an uninstalled source CLI."""
from pathlib import Path
import sys

base = Path(__file__).resolve().parent
runtime = base / "runtime"
if not (base / "manifest.json").is_file() or not runtime.is_symlink() or not (runtime / "loop_work/cli.py").is_file():
    print("loop-work installation is unavailable; rerun make install from its source checkout", file=sys.stderr)
    raise SystemExit(2)
sys.dont_write_bytecode = True
sys.path.insert(0, str(runtime))
from loop_work.cli import main
if __name__ == "__main__":
    raise SystemExit(main(entrypoint=str(base / "loop.py")))
'''
    skill = (source / "skills/loop-work/SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n"):
        raise LoopError("The loop-work skill needs YAML frontmatter")
    files = {install_name + "/loop.py": wrapper.encode()}
    links = {install_name + "/runtime": str(source)}
    for provider, discovery in (("codex", ".agents"), ("claude", ".claude")):
        adapter = install_name + "/adapters/" + provider
        files[adapter + "/SKILL.md"] = (skill if provider == "codex" else skill.replace(
            "---\n", "---\ndisable-model-invocation: true\n", 1)).encode()
        if provider == "codex":
            files[adapter + "/agents/openai.yaml"] = (source / "skills/loop-work/agents/openai.yaml").read_bytes()
        links[adapter + "/loop.py"] = "../../loop.py"
        links[adapter + "/rules"] = "../../runtime/rules"
        links[discovery + "/skills/loop-work"] = str(base / "adapters" / provider)
    if scope == "project":
        links[".loop/loop.py"] = "install/loop.py"
    return install_name, files, links


def _location(home=None, target=None, *, legacy=False):
    if target is not None:
        root = project_root(target)
        if root == SOURCE:
            raise LoopError("Use scripts/loop.py in the source repository; link into a separate project")
        return root, "project", ".loop/install/manifest.json"
    root = Path(home).expanduser().resolve() if home is not None else Path.home().resolve()
    if not root.is_dir():
        raise LoopError(f"Home directory does not exist: {root}")
    brand = "loop-dev" if legacy else "loop-work"
    return root, "user", f".local/share/{brand}/install/manifest.json"


def _read_link_manifest(root, scope, manifest_path):
    if manifest_path.is_symlink() or (manifest_path.exists() and not manifest_path.is_file()):
        raise LoopError(f"Conflicting install manifest: {manifest_path}")
    if not manifest_path.exists():
        return None
    old = read_json(manifest_path)
    if (not isinstance(old, dict) or old.get("version") != 2 or old.get("kind") != "links" or old.get("scope") != scope
            or old.get("root") != str(root) or not isinstance(old.get("source"), str)
            or not Path(old["source"]).is_absolute()
            or not all(isinstance(old.get(key), dict) for key in ("files", "links", "entry_blocks"))
            or not isinstance(old.get("directories"), list)
            or not all(isinstance(name, str) for name in old["directories"])):
        raise LoopError("Invalid link-install manifest; preserve it and reconcile before proceeding")
    # A manifest cannot authorize deleting arbitrary paths. Validate the fixed layout,
    # without needing the original source checkout to exist during uninstall.
    brand = "loop-dev" if ".agents/skills/loop-dev" in old["links"] else "loop-work"
    base = ".loop/install" if scope == "project" else f".local/share/{brand}/install"
    expected_files = {base + "/loop.py", base + "/adapters/codex/SKILL.md",
                      base + "/adapters/claude/SKILL.md", base + "/adapters/codex/agents/openai.yaml"}
    expected_links = {base + "/runtime": old["source"]}
    for provider, discovery in (("codex", ".agents"), ("claude", ".claude")):
        adapter = base + "/adapters/" + provider
        expected_links.update({adapter + "/loop.py": "../../loop.py", adapter + "/rules": "../../runtime/rules",
                               discovery + f"/skills/{brand}": str(root / adapter)})
    if scope == "project":
        expected_links[".loop/loop.py"] = "install/loop.py"
    blocks = {"AGENTS.md", "CLAUDE.md", ".gitignore"} if scope == "project" else set()
    if (set(old.get("files", {})) != expected_files or old.get("links") != expected_links
            or set(old.get("entry_blocks", {})) != blocks):
        raise LoopError("Invalid link-install ownership manifest; reconcile before proceeding")
    if (not all(isinstance(value, str) for value in old["files"].values())
            or not all(isinstance(value, dict) and isinstance(value.get("block"), str)
                       and value.get("prefix") in ("", "\n\n") for value in old["entry_blocks"].values())):
        raise LoopError("Invalid link-install resource records; reconcile before proceeding")
    for name, record in old["entry_blocks"].items():
        start, end = (IGNORE_START, IGNORE_END) if name == ".gitignore" else (START, END)
        start, end = start.replace("loop-work", brand), end.replace("loop-work", brand)
        block = record["block"]
        if not block.startswith(start) or not block.endswith(end) or block.count(start) != 1 or block.count(end) != 1:
            raise LoopError("Invalid managed block in install manifest")
    for name in old.get("directories", []):
        path = _safe_path(root, name)
        if path == root or not any((root / resource).is_relative_to(path) for resource in expected_files | set(expected_links) | {str(manifest_path.relative_to(root))}):
            raise LoopError("Invalid managed directory in install manifest")
    return old


def _preflight_owned(root, old):
    if old is None:
        return
    for name, expected in old["files"].items():
        path = _safe_path(root, name)
        if _present(path) and (path.is_symlink() or not path.is_file() or file_hash(path) != expected):
            raise LoopError(f"Local changes in managed file; preserve/reconcile before proceeding: {name}")
    for name, expected in old["links"].items():
        path = _safe_path(root, name)
        if _present(path) and (not path.is_symlink() or os.readlink(path) != expected):
            raise LoopError(f"Local changes in managed link; preserve/reconcile before proceeding: {name}")


def _entry_content(root, name, old, installing):
    path = _safe_path(root, name)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LoopError(f"Conflicting project instruction file: {name}")
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    previous = old.get("entry_blocks", {}).get(name) if old else None
    marker_start, marker_end, block = ((IGNORE_START, IGNORE_END, IGNORE_BLOCK) if name == ".gitignore"
                                      else (START, END, BLOCK))
    if previous and "loop-dev:" in previous["block"]:
        marker_start = marker_start.replace("loop-work", "loop-dev")
        marker_end = marker_end.replace("loop-work", "loop-dev")
    if marker_start in text or marker_end in text:
        if text.count(marker_start) != 1 or text.count(marker_end) != 1 or text.index(marker_start) > text.index(marker_end):
            raise LoopError(f"Malformed managed block in {name}")
        start, end = text.index(marker_start), text.index(marker_end) + len(marker_end)
        if previous is None or text[start:end] != previous["block"]:
            raise LoopError(f"Local edits or unowned loop-work block in {name}; preserve/reconcile before proceeding")
        if installing:
            return text[:start] + block.rstrip() + text[end:], {"block": block.rstrip(), "prefix": previous["prefix"]}
        prefix = previous["prefix"]
        if prefix and text[:start].endswith(prefix):
            start -= len(prefix)
        if text[end:].startswith("\n"):
            end += 1
        return text[:start] + text[end:], None
    if not installing:
        return text, None
    prefix = "\n\n" if text else ""
    return text + prefix + block, {"block": block.rstrip(), "prefix": prefix}


def _reject_legacy_install(root, scope):
    legacy_manifest = root / (".loop/install/manifest.json" if scope == "project"
                              else ".local/share/loop-dev/install/manifest.json")
    old_links = any(_present(root / provider / "skills/loop-dev") for provider in (".agents", ".claude"))
    old_block = scope == "project" and any(
        (root / name).is_file() and "loop-dev:start" in (root / name).read_text(encoding="utf-8")
        for name in ("AGENTS.md", "CLAUDE.md"))
    manifest_legacy = legacy_manifest.is_file() and (scope == "user" or
        ".agents/skills/loop-dev" in read_json(legacy_manifest).get("links", {}))
    if old_links or old_block or manifest_legacy:
        raise LoopError("Existing loop-dev installation: run python3 scripts/install.py --uninstall-legacy "
                        "(add --target PROJECT for project scope), then make install. "
                        "Copied/unowned installations need manual reconciliation; records are preserved.")


def _install_links(home=None, target=None):
    root, scope, manifest_name = _location(home, target)
    _reject_legacy_install(root, scope)
    manifest_path = _safe_path(root, manifest_name)
    old = _read_link_manifest(root, scope, manifest_path)
    _preflight_owned(root, old)
    _, files, links = _link_layout(root, scope, SOURCE)
    writes = {}
    for name, data in files.items():
        path = _safe_path(root, name)
        if _present(path) and (old is None or name not in old["files"]):
            raise LoopError(f"Unowned install destination; preserve it before link install: {name}")
        writes[path] = data
    for name in links:
        path = _safe_path(root, name)
        if _present(path) and (old is None or name not in old["links"]):
            hint = " Use the existing copied installation, or move its owned files aside before linking." if scope == "project" else ""
            raise LoopError(f"Unowned install destination; preserve it before link install: {name}.{hint}")
    blocks = {}
    if scope == "project":
        for name in ("AGENTS.md", "CLAUDE.md", ".gitignore"):
            content, blocks[name] = _entry_content(root, name, old, True)
            writes[root / name] = content.encode()
    directories = set(old.get("directories", [])) if old else set()
    for path in [*writes, *[root / name for name in links], manifest_path]:
        for parent in path.parents:
            if parent == root:
                break
            if not parent.exists():
                directories.add(str(parent.relative_to(root)))
    # Every ownership/path conflict above is checked before the first mutation.
    for path, data in writes.items():
        if not path.exists() or path.read_bytes() != data:
            _write_preserving_mode(path, data.decode())
    for name, target_name in links.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() and os.readlink(path) == target_name:
            continue
        if path.is_symlink():
            path.unlink()
        path.symlink_to(target_name, target_is_directory=name != ".loop/loop.py" and not name.endswith("/loop.py"))
    manifest = {"version": 2, "kind": "links", "scope": scope, "root": str(root), "source": str(SOURCE),
                "files": {name: file_hash(root / name) for name in files}, "links": links,
                "entry_blocks": blocks, "directories": sorted(directories)}
    if old != manifest:
        write_json(manifest_path, manifest)
    return manifest_path.parent


def _uninstall_links(home=None, target=None, *, legacy=False):
    root, scope, manifest_name = _location(home, target, legacy=legacy)
    manifest_path = _safe_path(root, manifest_name)
    old = _read_link_manifest(root, scope, manifest_path)
    if old is None:
        if scope == "project" and (root / ".loop/install.json").exists():
            raise LoopError("This project has a copied legacy installation; make uninstall only removes link installations. Preserve the copy or remove its manifest-owned resources after checking local changes.")
        return manifest_path.parent
    is_legacy = ".agents/skills/loop-dev" in old["links"]
    if is_legacy != legacy:
        raise LoopError("Installation name differs; use --uninstall-legacy for loop-dev, --uninstall for loop-work")
    _preflight_owned(root, old)
    writes = {}
    for name in old["entry_blocks"]:
        content, _ = _entry_content(root, name, old, False)
        state = root / ".loop/state"
        if name == ".gitignore" and state.is_dir() and any(state.iterdir()) and "/.loop/state/" not in content.splitlines():
            content += ("\n" if content and not content.endswith("\n") else "") + "/.loop/state/\n"
        writes[root / name] = content
    # Preflight instruction blocks and every owned resource before any removal.
    for path, content in writes.items():
        if path.exists() and path.read_bytes().decode("utf-8") != content:
            _write_preserving_mode(path, content)
    for name in [*old["links"], *old["files"]]:
        path = root / name
        if _present(path):
            path.unlink()
    manifest_path.unlink()
    for name in sorted(old["directories"], key=lambda value: len(Path(value).parts), reverse=True):
        path = root / name
        if path.is_symlink():
            raise LoopError(f"Managed directory changed during uninstall: {name}")
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass  # User files and knowledge/state keep their directories.
    return manifest_path.parent


def install_links(home=None, target=None):
    """Register user or project discovery links; keep knowledge/state untouched."""
    root, _, _ = _location(home, target)
    with _installation_lock(root):
        return _install_links(home, target)


def uninstall_links(home=None, target=None, *, legacy=False):
    root, _, _ = _location(home, target)
    with _installation_lock(root):
        return _uninstall_links(home, target, legacy=legacy)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, help="Project directory (without --link, retain the copied installer)")
    parser.add_argument("--home", type=Path, help="Override home for user-scope link installs and tests")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--link", action="store_true", help="Install discovery links (default scope: user)")
    modes.add_argument("--uninstall", action="store_true", help="Remove only resources owned by a link installation")
    modes.add_argument("--uninstall-legacy", action="store_true", help="Remove owned loop-dev links before installing loop-work; preserve records")
    args = parser.parse_args(argv)
    try:
        target = args.target
        if (args.link or args.uninstall or args.uninstall_legacy) and target is None and os.environ.get("LOOP_INSTALL_TARGET"):
            target = Path(os.environ["LOOP_INSTALL_TARGET"])
        if args.uninstall or args.uninstall_legacy:
            root = uninstall_links(args.home, target, legacy=args.uninstall_legacy)
            brand = "loop-dev" if args.uninstall_legacy else "loop-work"
            print(f"Removed {brand} discovery and runtime links from {root}; knowledge and task records are preserved.")
        elif args.link or target is None:
            root = install_links(args.home, target)
            print(f"Linked loop-work at {root}. Start with /loop-work in Claude Code or $loop-work in Codex.")
        else:
            if args.home is not None:
                raise LoopError("--home applies only to link installation; use --link with --target")
            root = install(target)
            print(f"Installed loop-work into {root}. Start with /loop-work in Claude Code or $loop-work in Codex; the whole conversation then uses the harness.")
        return 0
    except (LoopError, OSError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
