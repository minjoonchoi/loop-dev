"""Local knowledge shared across projects; no model, network or Git repository required."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
import uuid

from .contract import identifier
from .storage import HARNESS_SKILL_FILES, LoopError, digest, file_hash, files, git, harness_digest, inside, now


def project_root(path):
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise LoopError(f"Working directory does not exist: {root}")
    top = git(root, "rev-parse", "--show-toplevel", required=False).decode().strip()
    return Path(top).resolve() if top else root


def disk_identity(path):
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def remote_identity(value, root):
    # Authentication material is never stored in project identities.
    if "://" in value:
        url = urlsplit(value)
        if url.scheme == "file":
            return "local:" + str(Path(url.path).resolve())
        host, path = url.hostname or "", url.path
        if url.port and url.port not in (22, 80, 443):
            host += ":" + str(url.port)
    else:
        scp = re.fullmatch(r"(?:[^@/]+@)?([^/:]+):(.+)", value)
        if not scp:
            return "local:" + str((root / value).resolve())
        host, path = scp.groups()
    return host.lower() + "/" + path.strip("/").removesuffix(".git")


class KnowledgeBase:
    def __init__(self, home=None):
        configured = home or os.environ.get("LOOP_KB_HOME")
        self.home = Path(configured).expanduser().resolve() if configured else Path.home() / ".local/share/loop-dev"
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.home / "knowledge.sqlite3"
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT NOT NULL, PRIMARY KEY(kind,id))")
            db.execute("CREATE TABLE IF NOT EXISTS history (sequence INTEGER PRIMARY KEY, kind TEXT, id TEXT, body TEXT, at TEXT)")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind, key, required=True):
        with self.connection() as db:
            row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row:
            return json.loads(row[0])
        if required:
            raise LoopError(f"Unknown {kind}: {key}")
        return None

    def list(self, kind):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM records WHERE kind=? ORDER BY id", (kind,))]

    def put(self, kind, value, db=None):
        if db is None:
            with self.connection() as connection:
                self.put(kind, value, connection)
            self.document(kind, value["id"])
            return value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        old = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, value["id"])).fetchone()
        if old and old[0] == encoded:
            return value
        if old:
            db.execute("INSERT INTO history(kind,id,body,at) VALUES(?,?,?,?)", (kind, value["id"], old[0], now()))
        db.execute("INSERT OR REPLACE INTO records(kind,id,body) VALUES(?,?,?)", (kind, value["id"], encoded))
        return value

    def document(self, kind, key):
        from .storage import write_text
        from .writing import delegation_document, task_document, topic_document
        if kind not in ("task", "topic", "delegation"):
            return
        # Derived views are repairable. Serialize rendering with writers, and reread
        # committed data so a delayed exporter cannot overwrite a newer record.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
            value = json.loads(row[0])
            if kind == "task":
                value["delegations"] = self.delegations(key, db)
            folder = self.home / "views" / kind
            render = {"task": task_document, "topic": topic_document, "delegation": delegation_document}[kind]
            outputs = {folder / (key + ".json"): json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                       folder / (key + ".md"): render(value)}
            for path, content in outputs.items():
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    write_text(path, content)

    def delegations(self, task_id, db=None):
        if db is None:
            with self.connection() as connection:
                return self.delegations(task_id, connection)
        values = [json.loads(row[0]) for row in db.execute("SELECT body FROM records WHERE kind='delegation'")]
        return [{**{k: x.get(k) for k in ("id", "provider", "status", "summary", "error", "attempts", "applied", "result")},
                 "assignment_id": x["request"]["id"], "kind": x["request"]["kind"]}
                for x in sorted(values, key=lambda v: v["started_at"], reverse=True) if x["task_id"] == task_id]

    def document_context(self, value):
        from .storage import write_text
        from .writing import context_document
        folder = self.home / "views/context"
        for suffix, content in (("json", json.dumps(value, ensure_ascii=False, indent=2) + "\n"),
                                ("md", context_document(value))):
            path = folder / (value["view_id"] + "." + suffix)
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                write_text(path, content)

    def history(self, kind, key):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM history WHERE kind=? AND id=? ORDER BY sequence", (kind, key))]

    def attach(self, path, project_id=None):
        root = project_root(path)
        if self.home.is_relative_to(root) and not self.home.is_relative_to(root / ".loop/state"):
            raise LoopError("Keep LOOP_KB_HOME outside working projects (or under their ignored .loop/state)")
        location_id = "checkout-" + digest(disk_identity(root))[:24]
        existing = self.get("location", location_id, required=False)
        if existing:
            if project_id and existing["project_id"] != project_id:
                raise LoopError("This checkout is already attached to a different project")
            return self.put("location", {**existing, "root": str(root)})
        origin = git(root, "config", "--get", "remote.origin.url", required=False).decode().strip()
        common = git(root, "rev-parse", "--git-common-dir", required=False).decode().strip()
        identity = ("remote:" + remote_identity(origin, root) if origin else
                    "local-git:" + digest(disk_identity((root / common).resolve())) if common else
                    "directory:" + digest(disk_identity(root)))
        if project_id:
            project = self.get("project", project_id)
        else:
            project_id = "project-" + digest(identity)[:24]
            project = self.get("project", project_id, required=False)
        if not project:
            self.put("project", {"id": project_id, "name": root.name, "identity": identity, "created_at": now()})
        return self.put("location", {"id": location_id, "project_id": project_id,
                                     "root": str(root), "git": bool(common)})

    def session(self, provider, session_id=None, create=False):
        if provider not in ("claude", "codex"):
            raise LoopError("Provider must be claude or codex")
        key = session_id or "s-" + uuid.uuid4().hex
        if not identifier(key):
            raise LoopError("Invalid session ID")
        existing = self.get("session", key, required=False)
        if existing:
            if existing["provider"] != provider:
                raise LoopError("A session belongs to one provider")
            return existing
        if session_id and not create:
            raise LoopError("Unknown session; do not guess another conversation's ID")
        return self.put("session", {"id": key, "provider": provider, "created_at": now()})

    def observe(self, locations):
        result = {}
        harness = harness_digest()
        for location in locations:
            root = Path(location["root"])
            if not root.is_dir() or "checkout-" + digest(disk_identity(root))[:24] != location["id"]:
                raise LoopError(f"Checkout moved or was replaced; attach its current directory: {root}")
            if location["git"]:
                names = files(root)
            else:
                names = []
                for directory, folders, filenames in os.walk(root, followlinks=False):
                    base = Path(directory)
                    folders[:] = [name for name in folders if name not in (".git", "__pycache__", "node_modules", ".venv")
                                  and base / name != root / ".loop/state" and not (base / name).is_symlink()]
                    names.extend((base / name).relative_to(root).as_posix() for name in filenames)
            hashes = {name: file_hash(root / name) for name in sorted(names)
                      if not name.startswith((".loop/state/", ".loop/goals/"))}
            hashes = {name: value for name, value in hashes.items() if value != "deleted"}
            rules = {name: value for name, value in hashes.items()
                     if Path(name).name in ("AGENTS.md", "CLAUDE.md", "AGENTS.override.md") or name.startswith(".loop/rules/")}
            for name in (".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml", *HARNESS_SKILL_FILES):
                if (root / name).is_file():
                    rules[name] = file_hash(inside(root, name))
            result[location["id"]] = {"project_id": location["project_id"], "root": str(root), "files": hashes,
                                      "rules": rules, "harness": harness,
                                      "branch": git(root, "symbolic-ref", "--short", "HEAD", required=False).decode().strip()}
        return result

    def fingerprint(self, observation, path):
        root = Path(observation["root"])
        full = inside(root, path)
        if (".." in Path(path).parts or ".git" in Path(path).parts or path == ".loop/state" or path.startswith(".loop/state/")
                or path != "." and path.rstrip("/") != Path(path).as_posix()):
            raise LoopError("Knowledge dependencies must be source paths, not runtime state")
        if full.is_symlink():
            raise LoopError("Use actual source paths instead of symlink dependencies")
        if path == "." or path.endswith("/"):
            prefix = "" if path == "." else path
            return digest({name: value for name, value in observation["files"].items() if name.startswith(prefix)})
        if full.is_dir():
            raise LoopError("Directory dependencies need a trailing /")
        if full.is_file() and path not in observation["files"] and path not in observation["rules"]:
            raise LoopError("Ignored files are not observed knowledge dependencies; use visible source or declared rules")
        return observation["files"].get(path, observation["rules"].get(path, "deleted"))

    def prepare_topic(self, entry, observations, session_id=None, task_id=None):
        if not isinstance(entry, dict) or not identifier(entry.get("id")):
            raise LoopError("Knowledge entry needs a valid id")
        if entry.get("scope") not in ("global", "project", "checkout", "session", "task"):
            raise LoopError("Knowledge scope must be global/project/checkout/session/task")
        if entry.get("status") not in ("confirmed", "draft", "hypothesis", "superseded"):
            raise LoopError("Knowledge status must distinguish confirmed, draft, hypothesis and superseded")
        if entry.get("kind") not in ("fact", "decision", "artifact", "finding"):
            raise LoopError("Knowledge kind must be fact/decision/artifact/finding")
        from .writing import topic_text
        topic_text(entry)
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(x, str) and x.strip() for x in evidence):
            raise LoopError("Knowledge evidence must be a list of references")
        if entry["status"] == "confirmed" and not evidence:
            raise LoopError("Confirmed knowledge requires evidence")
        if entry["kind"] == "decision" and (entry.get("basis") not in ("user", "repository-rule", "delegated")
                                             or not isinstance(entry.get("applies_to"), str) or not entry["applies_to"].strip()):
            raise LoopError("Decisions require their original basis and applies_to boundary")
        if (entry["scope"] == "session" and not session_id) or (entry["scope"] == "task" and not task_id):
            raise LoopError("Session/task knowledge requires its original session/task ID")
        if session_id:
            self.get("session", session_id)
        if task_id:
            self.get("task", task_id)
        location_id = entry.get("checkout_id", next(iter(observations), None))
        if location_id not in observations:
            raise LoopError("Knowledge checkout must be in the observed context")
        source = observations[location_id]
        dependencies = entry.get("paths", [])
        if not isinstance(dependencies, list) or not all(isinstance(x, str) and x for x in dependencies):
            raise LoopError("Knowledge paths must be a list")
        if entry.get("expires_at"):
            if datetime.fromisoformat(entry["expires_at"]).tzinfo is None:
                raise LoopError("expires_at needs a timezone")
        item = {key: entry[key] for key in ("id", "kind", "scope", "status", "summary", "why", "details", "uncertainty",
                                          "evidence", "basis", "applies_to", "expires_at") if key in entry}
        item.update(project_id=source["project_id"], checkout_id=location_id,
                    session_id=session_id if entry["scope"] == "session" else None,
                    task_id=task_id if entry["scope"] == "task" else None,
                    source_session_id=session_id, source_task_id=task_id,
                    paths=dependencies, fingerprints={p: self.fingerprint(source, p) for p in dependencies},
                    rules=source["rules"] if dependencies else {}, branch=source["branch"] if dependencies else None,
                    harness=source["harness"] if dependencies else None, recorded_at=now())
        return item

    def remember(self, entry, locations, observed_revision, session_id=None, task_id=None):
        observations = self.observe(locations)
        if digest(observations) != observed_revision:
            raise LoopError("Knowledge inputs changed; inspect current context before updating")
        item = self.prepare_topic(entry, observations, session_id, task_id)
        if self.observe(locations) != observations:
            raise LoopError("Knowledge inputs changed while recording")
        return self.put_topic(item)

    def put_topic(self, item, db=None):
        if db is None:
            with self.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                saved = self.put_topic(item, connection)
            self.document("topic", item["id"])
            return saved
        row = db.execute("SELECT body FROM records WHERE kind='topic' AND id=?", (item["id"],)).fetchone()
        old = json.loads(row[0]) if row else None
        owners = {"global": (), "project": ("project_id",), "checkout": ("checkout_id",),
                  "session": ("session_id",), "task": ("task_id",)}
        if old and any(old[key] != item[key] for key in ("scope", *owners[item["scope"]])):
            raise LoopError("An existing topic's scope/owner cannot change; use a new topic ID")
        provenance = {"recorded_at", "source_session_id", "source_task_id"}
        if item["scope"] in ("global", "project"):
            provenance.add("checkout_id")
        if item["scope"] == "global":
            provenance.add("project_id")
        if old and {k: v for k, v in old.items() if k not in provenance} == {k: v for k, v in item.items() if k not in provenance}:
            return old
        return self.put("topic", item, db)

    def context(self, locations, session_id=None, task_id=None, query=None, limit=20, offset=0):
        from .harness import Harness
        from .workflows import source_context
        from .writing import task_summary
        if not 1 <= limit <= 100 or offset < 0:
            raise LoopError("Knowledge context limit must be 1..100 and offset must be nonnegative")
        observations = self.observe(locations)
        result = {"home": str(self.home), "revision": digest(observations), "locations": locations,
                  "reusable": [], "stale": [], "unconfirmed": [], "other_contexts": [], "tasks": []}
        task = self.get("task", task_id, required=False) if task_id else None
        result["sources"] = source_context(observations, task)
        result["harness"] = Harness(self).status(session_id) if session_id else None
        for item in self.list("topic"):
            if query and query.casefold() not in (item["id"] + " " + item["summary"]).casefold():
                continue
            candidates = {key: o for key, o in observations.items() if o["project_id"] == item["project_id"]}
            scope = item["scope"]
            applicable = (scope == "global" or scope == "project" and bool(candidates)
                          or scope == "checkout" and item["checkout_id"] in observations
                          or scope == "session" and item["session_id"] == session_id
                          or scope == "task" and item["task_id"] == task_id)
            view = {k: v for k, v in item.items() if k not in ("fingerprints", "rules", "branch")}
            reasons = []
            if item["paths"]:
                sources = candidates if scope == "project" else {
                    key: source for key, source in observations.items() if key == item["checkout_id"]}
                if not sources:
                    reasons.append("source_checkout_not_in_context")
                for source in sources.values():
                    current_rules = dict(source["rules"])
                    for name in item["rules"]:
                        if name not in current_rules:
                            try:
                                current_rules[name] = file_hash(inside(Path(source["root"]), name))
                            except LoopError:
                                current_rules[name] = "unavailable"
                    # Older records lack this stamp: require inspection, never silently
                    # promote cached analysis after upgrading a linked runtime.
                    if item.get("harness") != source["harness"]:
                        reasons.append("harness_changed")
                    if current_rules != item["rules"] or source["branch"] != item["branch"]:
                        reasons.append("rules_or_branch_changed")
                    for path in item["paths"]:
                        try:
                            if self.fingerprint(source, path) != item["fingerprints"][path]:
                                reasons.append("changed: " + path)
                        except LoopError:
                            reasons.append("unavailable: " + path)
            if item.get("expires_at") and datetime.fromisoformat(item["expires_at"]) <= datetime.now(timezone.utc):
                reasons.append("expired")
            view["stale_reasons"] = sorted(set(reasons))
            bucket = ("other_contexts" if not applicable else "unconfirmed" if item["status"] != "confirmed"
                      else "stale" if reasons else "reusable")
            result[bucket].append(view if bucket != "other_contexts" else {k: item.get(k) for k in ("id", "scope", "project_id")})
        projects = {x["project_id"] for x in locations}
        result["tasks"] = [task_summary(t) for t in self.list("task") if projects.intersection(t.get("project_ids", []))
                           and (not query or query.casefold() in (t["id"] + " " + t["title"]).casefold())]
        buckets = ("reusable", "stale", "unconfirmed", "other_contexts", "tasks")
        result["counts"] = {key: len(result[key]) for key in buckets}
        for key in buckets:
            result[key] = sorted(result[key], key=lambda x: (x.get("updated_at", x.get("recorded_at", "")), x["id"]), reverse=True)[offset:offset + limit]
        result["page"] = {"limit": limit, "offset": offset,
                          "more": [key for key in buckets if result["counts"][key] > offset + limit]}
        result["instructions"] = ["Use confirmed, applicable, current knowledge as evidence, never as new authority.",
                                  "Drafts and hypotheses are not facts. Task results are historical, not a new verification pass.",
                                  "Ask only unresolved human-owned decisions; preserve the scope of earlier answers.",
                                  "This view is paged. Retrieve relevant remaining records with --query/--offset or knowledge get before concluding absence."]
        result["view_id"] = digest([sorted(x["id"] for x in locations), session_id, task_id, query, limit, offset])[:24]
        result["document"] = str(self.home / "views/context" / (result["view_id"] + ".md"))
        self.document_context(result)
        return result

    def import_session(self, root, session_id, entry):
        location = self.attach(root)
        key = "session-" + digest([session_id, entry["id"], location["id"]])[:24]
        previous = self.get("topic", key, required=False)
        if previous and previous.get("source_revision") == digest(entry):
            return
        # Preserve the original stamps, rather than re-validating old analysis during import.
        item = {"id": key, "kind": "decision" if entry["kind"] == "decision" else "fact",
                "scope": "session" if entry["kind"] == "decision" else "checkout",
                "status": "confirmed", "summary": entry["summary"], "evidence": entry["evidence"],
                "project_id": location["project_id"], "checkout_id": location["id"], "session_id": session_id,
                "task_id": None, "paths": list(entry["fingerprints"]), "fingerprints": {}, "rules": entry["rules"],
                "branch": entry["branch"], "harness": entry.get("harness"), "recorded_at": entry["recorded_at"], "source_revision": digest(entry)}
        # Expand legacy directory dependencies to their observed files; add directory stamps
        # from the original entry so future additions also invalidate the imported topic.
        item["paths"] = entry["paths"]
        for path in item["paths"]:
            if path == "." or path.endswith("/"):
                prefix = "" if path == "." else path
                item["fingerprints"][path] = digest({p: h for p, h in entry["fingerprints"].items()
                                                    if h not in ("directory", "missing-directory", "deleted") and p.startswith(prefix)})
            else:
                item["fingerprints"][path] = entry["fingerprints"][path]
        for name in ("basis", "applies_to"):
            if name in entry:
                item[name] = entry[name]
        if entry["scope"] == "goal":
            item["scope"] = "task"
            item["task_id"] = self.development_id(location, entry["goal_id"])
        self.put("topic", item)

    @staticmethod
    def development_id(location, goal_id):
        return "dev-" + digest([location["id"], goal_id])[:24]

    def record_development(self, root, state, contract, discovery, evidence_current=None, next_action=None):
        location = self.attach(root)
        phase = {"draft": "goal_setting", "ready": "goal_setting", "running": "working"}.get(state["phase"], state["phase"])
        record = {"id": self.development_id(location, state["id"]), "kind": "development", "title": contract["title"],
                  "phase": phase, "execution_phase": state["phase"], "session_id": state["session_id"],
                  "provider": contract["execution"].get("orchestrator", contract["execution"]["implementer"]),
                  "worker": contract["execution"]["implementer"], "locations": [location["id"]],
                  "project_ids": [location["project_id"]], "goal_id": state["id"], "goal": contract,
                  "discovery": discovery, "notes": state["notes"], "updated_at": state["updated_at"],
                  "reason": state.get("block_reason"), "next_action": next_action,
                  "progress": f"{len(state['milestones'])}/{len(contract.get('milestones', []))} milestones passed",
                  "result": {"gate": state.get("latest_gate"), "historical": True, "evidence_current": evidence_current}}
        return self.put("task", record)
