"""Bounded navigation to observed project inputs; never a workflow or authority source."""

from __future__ import annotations

from pathlib import Path
import re

from .storage import LoopError, digest


ROLE_WORDS = {
    "requirements": ("prd", "requirement", "requirements", "요구사항", "요구 분석"),
    "planning": ("plan", "planning", "spec", "specs", "specification", "기획", "명세", "화면설계"),
    "mockup": ("mockup", "mockups", "prototype", "wireframe", "목업", "프로토타입"),
    "api": ("api", "openapi", "swagger", "graphql", "protobuf", "interface", "interfaces", "인터페이스", "연동"),
    "frontend": ("frontend", "front-end", "fe", "프론트엔드", "프런트엔드"),
    "backend": ("backend", "back-end", "be", "백엔드"),
    "architecture": ("architecture", "architectures", "adr", "아키텍처", "구조", "설계"),
}
DOCUMENT_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc", ".pdf", ".docx", ".html", ".htm"}
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".java", ".kt", ".go",
                 ".rs", ".rb", ".php", ".cs", ".swift", ".c", ".cpp", ".h", ".sql"}
GOAL_KEYS = {"title", "outcome", "deliverable", "description", "scenario", "in_scope", "requirements",
             "acceptance", "criteria", "target", "goal"}
SKIP_PARTS = {"node_modules", "__pycache__", "venv", "dist", "build", "coverage", "vendor"}
SENSITIVE_WORDS = {"secret", "secrets", "credential", "credentials", "keyfile", "keystore"}


def _mentions(text, word):
    if word.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def _goal_text(goal):
    """Read only goal-facing fields, without including decisions, provider settings or logs."""
    values = []
    remaining = 4000

    def collect(value, depth=0):
        nonlocal remaining
        if remaining <= 0 or depth > 6:
            return
        if isinstance(value, str):
            values.append(value[:remaining])
            remaining -= len(values[-1])
        elif isinstance(value, dict):
            for key in sorted(set(value) & GOAL_KEYS):
                collect(value[key], depth + 1)
        elif isinstance(value, list):
            for item in value[:100]:
                collect(item, depth + 1)

    collect(goal)
    return " ".join(values).casefold()


def _visible_source(root, name):
    path = Path(name)
    parts = path.parts
    if (not parts or path.is_absolute() or name != path.as_posix() or
            any(part in (".", "..") or part.startswith(".") or part.casefold() in SKIP_PARTS for part in parts)):
        return False
    lowered = name.casefold()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    if (words & SENSITIVE_WORDS or path.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".keystore"}
            or path.name.casefold() in {"agents.md", "agents.override.md", "claude.md", "id_rsa", "id_ed25519"}):
        return False
    # No symlink traversal, including a directory component inside the observed root.
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            return False
    return target.is_file() and target.resolve().is_relative_to(root.resolve())


def _roles(name):
    path = Path(name)
    text = name.casefold()
    suffix = path.suffix.casefold()
    roles = set()
    if suffix in DOCUMENT_SUFFIXES:
        for role in ("requirements", "planning", "mockup", "architecture"):
            if any(_mentions(text, word) for word in ROLE_WORDS[role]):
                roles.add(role)
        if re.fullmatch(r"requirements(?:[-_.].+)?\.txt", path.name.casefold()):
            roles.discard("requirements")  # Python dependency manifests are not a PRD.
    if suffix in {".html", ".htm"}:
        roles.add("mockup")  # A navigation hint; a page may be implementation instead.
    if (any(_mentions(text, word) for word in ROLE_WORDS["api"] if word != "연동")
            or suffix in {".graphql", ".gql", ".proto"}):
        roles.add("api")
    if suffix in CODE_SUFFIXES | {".css", ".scss", ".html", ".htm"}:
        for role in ("frontend", "backend"):
            if any(_mentions(text, word) for word in ROLE_WORDS[role]):
                roles.add(role)
        if suffix in {".jsx", ".tsx", ".vue", ".svelte", ".css", ".scss"}:
            roles.add("frontend")
        if any(_mentions(text, word) for word in ("server", "controller", "controllers", "routes", "models")):
            roles.add("backend")
    return sorted(roles) or ["source"]


def source_context(observations, goal=None, limit=24):
    """Rank references from explicit observations, without reading source contents.

    Roles and relevance come only from paths and selected goal fields. They do not
    attest document contents, product authority, completeness or consumer consistency.
    Unknown names remain selectable sources. Freshness uses the caller's observation.
    """
    if type(limit) is not int or not 1 <= limit <= 24:
        raise LoopError("Source context limit must be 1..24")
    text = _goal_text(goal)
    goal_roles = {role for role, words in ROLE_WORDS.items() if any(_mentions(text, word) for word in words)}
    goal_words = {word for word in re.findall(r"[\w]+", text) if len(word) >= 3}
    rows = []
    counts = {role: 0 for role in (*ROLE_WORDS, "source")}
    for location_id, observed in sorted(observations.items()):
        root = Path(observed["root"])
        for name, fingerprint in sorted(observed["files"].items()):
            if fingerprint == "deleted" or not _visible_source(root, name):
                continue
            roles = _roles(name)
            for role in roles:
                counts[role] += 1
            role_match = len(set(roles) & goal_roles)
            path_match = len(goal_words & set(re.findall(r"[\w]+", name.casefold())))
            row = {"checkout_id": location_id, "path": name, "ref": str(root / name),
                   "fingerprint": fingerprint, "roles": roles,
                   "relevance": "goal_role_hint" if role_match else "goal_path_hint" if path_match else "unranked"}
            if observed.get("project_id"):
                row["project_id"] = observed["project_id"]
            rows.append(((-role_match, -path_match, roles == ["source"], location_id, name), row))
    rows.sort(key=lambda item: item[0])
    return {"basis": "path_hints", "revision": digest(observations), "goal_roles": sorted(goal_roles),
            "role_counts": counts, "count": len(rows), "limit": limit, "truncated": len(rows) > limit,
            "sources": [row for _, row in rows[:limit]],
            "instructions": [
                "Paths and roles are navigation hints, not inspected facts, approved requirements or new authority.",
                "Read selected files within an explicit assignment scope; reuse current knowledge and investigate relevant gaps or changes.",
                "The list is bounded and names may be unclassified. No match does not prove absence; inspect the explicit project roots as needed.",
                "Refresh context after source edits. Record complete read dependencies; only a new inspection can refresh stale conclusions.",
            ]}
