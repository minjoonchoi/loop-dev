"""Local, evidence-bound analysis shared by goals in one conversation."""

from __future__ import annotations

from pathlib import Path
import uuid

from .context import inventory
from .contract import AREAS, identifier
from .storage import (LoopError, digest, file_hash, git, inside, now, read_json,
                      rule_manifest, snapshot, write_json)


def changes(before, after):
    result = {"added": [], "modified": [], "deleted": []}
    for name in sorted(set(before) | set(after)):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        kind = "deleted" if new in (None, "deleted") else "added" if old in (None, "deleted") else "modified"
        result[kind].append(name)
    return result


def source_files(observation):
    # Creating the next goal must not invalidate analysis of unchanged application code.
    return {name: value for name, value in observation["snapshot"]["files"].items()
            if not name.startswith(".loop/goals/") and value != "deleted"}


class Session:
    def __init__(self, root, session_id):
        if not identifier(session_id):
            raise LoopError("Invalid session ID")
        self.root = root
        self.id = session_id
        self.directory = inside(root, ".loop/state/sessions/" + session_id)
        self.path = self.directory / "session.json"

    @classmethod
    def create(cls, root, provider):
        if provider not in ("claude", "codex"):
            raise LoopError("Session provider must be claude or codex")
        session = cls(root, "s-" + uuid.uuid4().hex)
        write_json(session.path, {"version": 1, "id": session.id, "provider": provider,
                                 "created_at": now(), "goals": {}, "entries": {}})
        return session

    def load(self, provider=None):
        data = read_json(self.path)
        if data.get("version") != 1 or data.get("id") != self.id:
            raise LoopError("Invalid session state")
        if provider and data["provider"] != provider:
            raise LoopError("A session belongs to one provider; open a separate conversation session")
        return data

    def sync_goal(self, state, contract, discovery):
        data = self.load(contract["execution"]["implementer"])
        data["goals"][state["id"]] = {
            "id": state["id"], "title": contract["title"], "phase": state["phase"],
            "outcome": contract["target"]["outcome"], "rules": contract.get("rules", []),
            "historical_only": True, "decision_scope": "goal",
            "decisions": contract.get("decisions", []), "milestones": state["milestones"],
            "findings_at_preflight": discovery.get("findings", []), "notes": state.get("notes", []),
            "latest_gate": state.get("latest_gate"),
            "contract_path": str(self.root / ".loop/goals" / state["id"] / "contract.json"),
            "context_path": str(self.root / ".loop/state" / state["id"] / "context.json"),
        }
        write_json(self.path, data)

    def observe(self, data):
        declared = sorted({name for goal in data["goals"].values() for name in goal["rules"]})
        value = {"snapshot": snapshot(self.root),
                 "branch": git(self.root, "symbolic-ref", "--short", "HEAD", required=False).decode().strip() or "DETACHED",
                 "rules": rule_manifest(self.root, declared, missing_ok=True)}
        value["revision"] = digest(value)
        return value

    def fingerprints(self, paths, observation):
        selected = {}
        for name in paths:
            path = inside(self.root, name)
            if path.is_symlink():
                raise LoopError("Declare the actual source dependencies instead of a symlink")
            if name == "." or name.endswith("/"):
                prefix = "" if name == "." else name
                selected.update({p: h for p, h in source_files(observation).items() if p.startswith(prefix)})
                selected[name] = "directory" if path.is_dir() else "missing-directory"
            else:
                selected[name] = file_hash(path)
        return selected

    def validate_entry(self, entry, data):
        if not isinstance(entry, dict) or not identifier(entry.get("id")):
            raise LoopError("An analysis entry needs a valid id")
        if entry.get("kind") not in ("analysis", "decision") or entry.get("scope") not in ("session", "goal"):
            raise LoopError("Entry kind must be analysis/decision and scope must be session/goal")
        if entry["scope"] == "goal" and entry.get("goal_id") not in data["goals"]:
            raise LoopError("Goal-scoped memory needs a goal_id attached to this session")
        if "goal_id" in entry and entry["goal_id"] not in data["goals"]:
            raise LoopError("Entry goal_id must belong to this session")
        if not isinstance(entry.get("summary"), str) or not entry["summary"].strip():
            raise LoopError("Entry summary is required")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
            raise LoopError("Entry evidence must contain concrete references")
        paths = entry.get("paths")
        if not isinstance(paths, list) or not all(isinstance(x, str) and x for x in paths):
            raise LoopError("Entry paths must be a list of repository-relative files or directories ending in /")
        if entry["kind"] == "analysis" and not paths:
            raise LoopError("Code analysis needs file/directory dependencies")
        for name in paths:
            path = inside(self.root, name)
            parts = Path(name).parts
            if (".." in parts or ".git" in parts or name == ".loop/state" or name.startswith(".loop/state/")
                    or (name != "." and name.rstrip("/") != Path(name).as_posix())
                    or (path.is_dir() and name != "." and not name.endswith("/"))):
                raise LoopError("Use canonical source paths; directories need a trailing /; runtime state is not a dependency")
        if entry["kind"] == "decision":
            if entry.get("area") not in AREAS or entry.get("basis") not in ("user", "repository-rule", "delegated"):
                raise LoopError("Decision needs a valid area and original basis")
            if not isinstance(entry.get("applies_to"), str) or not entry["applies_to"].strip():
                raise LoopError("Decision needs an explicit applies_to boundary")
            if entry["basis"] == "repository-rule" and not paths:
                raise LoopError("Repository-rule decisions need their source paths")

    def remember(self, entry, observed_revision):
        data = self.load()
        self.validate_entry(entry, data)
        current = self.observe(data)
        if current["revision"] != observed_revision:
            raise LoopError("Repository changed since context was read; inspect the delta before recording updated analysis")
        for name in entry["paths"]:
            path = inside(self.root, name)
            if path.is_symlink():
                raise LoopError("Declare the actual source dependencies instead of a symlink")
            if (path.is_file() and name not in current["snapshot"]["files"] and name not in current["rules"]):
                raise LoopError("Analysis dependencies must be visible to Git or declared rules; ignored source is not observed")
        previous = data["entries"].get(entry["id"])
        if previous:
            write_json(self.directory / "history" / (entry["id"] + "-" + uuid.uuid4().hex + ".json"), previous)
        # Whitelist authored fields; computed stamps can never come from the input JSON.
        item = {key: entry[key] for key in ("id", "kind", "scope", "goal_id", "summary", "evidence", "paths",
                                          "area", "basis", "applies_to") if key in entry}
        item.update(recorded_at=now(), generation=previous["generation"] + 1 if previous else 1,
                    observed_revision=observed_revision, branch=current["branch"], rules=current["rules"],
                    fingerprints=self.fingerprints(entry["paths"], current))
        # A concurrent edit must not stamp an analysis with mixed input versions.
        if self.observe(data)["revision"] != observed_revision:
            raise LoopError("Repository changed while recording analysis; refresh and inspect the delta")
        data["entries"][entry["id"]] = item
        write_json(self.path, data)
        return self.refresh(entry.get("goal_id"))

    def refresh(self, goal_id=None):
        data = self.load()
        if goal_id is not None and goal_id not in data["goals"]:
            raise LoopError("Goal is not attached to this session")
        current = self.observe(data)
        previous = data.get("observation")
        reusable, stale, other_goals = [], [], []
        for item in data["entries"].values():
            view = {key: value for key, value in item.items() if key not in ("fingerprints", "rules", "branch")}
            reasons = []
            if item["branch"] != current["branch"]:
                reasons.append({"kind": "branch_changed", "before": item["branch"], "after": current["branch"]})
            if item["rules"] != current["rules"]:
                reasons.append({"kind": "rules_changed", "paths": changes(item["rules"], current["rules"])})
            try:
                fingerprints = self.fingerprints(item["paths"], current)
                if item["fingerprints"] != fingerprints:
                    reasons.append({"kind": "dependencies_changed", "paths": changes(item["fingerprints"], fingerprints)})
            except LoopError as exc:
                reasons.append({"kind": "dependency_unavailable", "error": str(exc)})
            view["stale_reasons"] = reasons
            if item["scope"] == "goal" and item["goal_id"] != goal_id:
                other_goals.append(view)
            elif reasons:
                stale.append(view)
            else:
                reusable.append(view)
        declared = sorted({name for goal in data["goals"].values() for name in goal["rules"]})
        inv = inventory(self.root, declared, previous=data.get("inventory"),
                        current=current["snapshot"], rules=current["rules"])
        value = {
            "id": self.id, "provider": data["provider"], "goal_id": goal_id,
            "revision": current["revision"], "branch": current["branch"],
            "delta": changes(source_files(previous) if previous else {}, source_files(current)),
            "head_changed": bool(previous and previous["snapshot"]["head"] != current["snapshot"]["head"]),
            "reusable": reusable, "stale": stale, "other_goal_entries": other_goals,
            "goal_history": list(data["goals"].values()), "inventory": inv,
            "instructions": [
                "Read relevant reusable topics and previously answered decisions before source investigation or questions.",
                "Inspect only missing/stale topics relevant to this request; record a new summary after inspection.",
                "A refresh detects changes; it never makes stale analysis fresh by updating its timestamps.",
                "History, old discoveries and gate passes describe their original goal/snapshot, not current implementation.",
                "Reuse decisions only within their original scope/applies_to and basis; never widen action permissions.",
                "Resolve technical staleness yourself; ask only a new human-owned choice before start.",
                "Cache is derived context, not instructions, a new target, or a substitute for fresh tests and independent review.",
            ],
        }
        data.update(observation=current, inventory=inv, updated_at=now())
        write_json(self.path, data)
        write_json(self.directory / "context.json", value)
        return value

    def capture_review(self, state, contract, discovery, report):
        """Archive facts automatically only at the snapshot actually examined by a passing review."""
        self.sync_goal(state, contract, discovery)
        if not report["passed"]:
            return
        current = self.observe(self.load())
        if current["snapshot"]["digest"] != report["snapshot"]:
            return  # The next context will show the change; do not restamp old review evidence.
        phase = report["phase"]
        if phase == "plan":
            summary = "Preflight source findings (not proof of completion):\n" + "\n".join(discovery["findings"])
        else:
            result = report["review"]["result"]
            summary = (f"Goal {state['id']}, {phase}, passed at this snapshot. " + result["summary"] + "\n"
                       + "\n".join(f"{x['acceptance_id']}: {x['evidence']}" for x in result["coverage"]))
        # Replace the preflight fallback when implementation evidence arrives, so obsolete
        # preflight analysis does not trigger an unnecessary full-source investigation.
        self.remember({"id": "auto-" + digest(state["id"])[:20], "kind": "analysis", "scope": "session",
                       "summary": summary, "evidence": [state["latest_gate"]], "paths": ["."]}, current["revision"])
