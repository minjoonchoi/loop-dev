"""Validate target completeness independently of implementation maturity."""

from __future__ import annotations

import re

from .storage import LoopError, inside


AREAS = ("scope", "architecture", "data", "interface", "dependencies", "security",
         "verification", "operations", "delivery", "autonomy")
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def template(goal_id, title, implementer):
    return {
        "version": 1, "id": goal_id, "title": title,
        "target": {"outcome": "", "users": [], "in_scope": [], "out_of_scope": [],
                   "requirements": [], "acceptance": []},
        "decisions": [{"area": area, "answer": "", "basis": "", "rationale": ""}
                      for area in AREAS],
        "open_questions": [], "rules": [], "checks": [], "milestones": [],
        "execution": {"orchestrator": implementer, "implementer": implementer, "max_worker_calls": 40,
                      "worker_timeout_seconds": 600, "max_gate_attempts": 3,
                      "review_timeout_seconds": 600, "max_run_seconds": 14400},
    }


def validate(contract, discovery, root):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def strings(value, allow_empty=False):
        return isinstance(value, list) and (allow_empty or bool(value)) and all(text(x) for x in value)

    def records(value, label, allow_empty=False):
        if not isinstance(value, list) or (not value and not allow_empty) or not all(isinstance(x, dict) for x in value):
            errors.append(f"{label} must be a {'possibly empty ' if allow_empty else 'nonempty '}object list")
            return []
        return value

    def index(rows, label):
        keys = [row.get("id") for row in rows]
        if not all(identifier(key) for key in keys):
            errors.append(f"{label} IDs must match {ID.pattern}")
            return set()
        require(len(keys) == len(set(keys)), f"{label} IDs must be unique")
        return set(keys)

    def refs(value, options, label):
        if not strings(value):
            errors.append(f"{label} must be a nonempty list of IDs")
            return set()
        require(len(value) == len(set(value)), f"{label} contains duplicate IDs")
        require(set(value) <= options, f"{label} has unknown IDs: {set(value) - options}")
        return set(value)

    if not isinstance(contract, dict) or not isinstance(discovery, dict):
        return ["contract and discovery must be JSON objects"]
    require(type(contract.get("version")) is int and contract["version"] == 1, "Unsupported contract version")
    require(identifier(contract.get("id")), "Invalid goal id")
    require(text(contract.get("title")), "title is required")
    target = contract.get("target")
    if not isinstance(target, dict):
        return errors + ["target must be an object"]
    require(text(target.get("outcome")), "target.outcome is required")
    for key in ("users", "in_scope", "out_of_scope"):
        require(strings(target.get(key)), f"target.{key} must be explicit and nonempty")
    requirements = records(target.get("requirements"), "requirements")
    req_ids = index(requirements, "requirements")
    for item in requirements:
        require(text(item.get("description")), "Each requirement needs a description")
    checks = records(contract.get("checks"), "checks")
    check_ids = index(checks, "checks")
    for check in checks:
        require(strings(check.get("argv")), f"check {check.get('id')} needs a nonempty argv (no shell string)")
        require(check.get("kind") in ("acceptance", "regression"), "check.kind must be acceptance or regression")
        timeout = check.get("timeout_seconds")
        require(type(timeout) is int and 1 <= timeout <= 3600, "check timeout must be 1..3600 seconds")
        try:
            inside(root, check.get("cwd"))
        except LoopError as exc:
            errors.append(str(exc))
    acceptance = records(target.get("acceptance"), "acceptance")
    acceptance_ids = index(acceptance, "acceptance")
    covered_requirements, used_checks = set(), set()
    for item in acceptance:
        require(text(item.get("scenario")), "Each acceptance criterion needs an observable scenario")
        covered_requirements |= refs(item.get("requirement_ids"), req_ids, "acceptance.requirement_ids")
        used_checks |= refs(item.get("check_ids"), check_ids, "acceptance.check_ids")
    require(req_ids <= covered_requirements, f"Requirements without acceptance: {req_ids - covered_requirements}")
    unused = {x.get("id") for x in checks if x.get("kind") == "acceptance" and identifier(x.get("id"))} - used_checks
    require(not unused, f"Acceptance checks not mapped to a criterion: {unused}")
    milestones = records(contract.get("milestones"), "milestones")
    index(milestones, "milestones")
    covered_acceptance = set()
    for item in milestones:
        require(text(item.get("title")), "Each milestone needs a title")
        covered_acceptance |= refs(item.get("acceptance_ids"), acceptance_ids, "milestone.acceptance_ids")
    require(acceptance_ids <= covered_acceptance, f"Acceptance criteria without milestones: {acceptance_ids - covered_acceptance}")
    decisions = records(contract.get("decisions"), "decisions")
    areas = [d.get("area") for d in decisions]
    require(all(isinstance(x, str) for x in areas) and sorted(areas) == sorted(AREAS),
            "Exactly one decision per area is required: " + ", ".join(AREAS))
    for decision in decisions:
        require(text(decision.get("answer")) and text(decision.get("rationale")),
                f"Decision {decision.get('area')} needs an answer and rationale (explain N/A)")
        require(decision.get("basis") in ("user", "repository-rule", "delegated"),
                "Decision basis must be user, repository-rule, or delegated")
    require(contract.get("open_questions") == [], "Resolve every open question before execution")
    require(strings(contract.get("rules")), "Declare at least one rule file")
    if strings(contract.get("rules")):
        for rule in contract["rules"]:
            try:
                require(inside(root, rule).is_file(), f"Rule file is missing: {rule}")
            except LoopError as exc:
                errors.append(str(exc))
    execution = contract.get("execution", {})
    if not isinstance(execution, dict):
        return errors + ["execution must be an object"]
    require(execution.get("implementer") in ("claude", "codex"), "implementer must be claude or codex")
    require(execution.get("orchestrator", execution.get("implementer")) in ("claude", "codex"),
            "orchestrator must be claude or codex")
    for key, default, maximum in (("max_worker_calls", 40, 200), ("worker_timeout_seconds", 600, 3600)):
        val = execution.get(key, default)
        require(type(val) is int and 1 <= val <= maximum, f"execution.{key} must be 1..{maximum}")
    for key, maximum in (("max_gate_attempts", 20), ("review_timeout_seconds", 3600), ("max_run_seconds", 604800)):
        val = execution.get(key)
        require(type(val) is int and 1 <= val <= maximum, f"execution.{key} must be 1..{maximum}")
    require(text(discovery.get("baseline_head")), "discovery.baseline_head is required")
    require(strings(discovery.get("findings")), "discovery.findings must describe code and rule evidence")
    gaps = records(discovery.get("gaps"), "discovery.gaps")
    gap_ids = [gap.get("requirement_id") for gap in gaps]
    require(all(identifier(x) for x in gap_ids) and set(gap_ids) == req_ids and len(gap_ids) == len(req_ids),
            "discovery.gaps must cover every requirement exactly once")
    for gap in gaps:
        require(text(gap.get("current_state")) and text(gap.get("work_needed")),
                "Each gap needs current_state and work_needed; missing code does not lower the target")
    return errors
