"""Explicit economical worker profiles and goal-linked assignment boundaries.

Ranks are locally configured relative orderings, not prices or claims that a
particular model is available. Callers snapshot the validated policy before work;
worker dispatch must never reread the environment as a fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from .contract import identifier
from .providers import effort_args
from .storage import LoopError, digest, read_json
from .writing import concise


LEVELS = ("low", "medium", "high")
PREFLIGHT_CRITERION = "goal_setting"
POLICY_FIELDS = {"version", "profiles", "requirements"}
PROFILE_FIELDS = {"id", "provider", "model", "effort", "capability_rank", "cost_rank"}
PLANNING_FIELDS = {"goal_criteria", "rationale", "verification", "complexity", "risk", "uncertainty", "evidence"}
MAX_PROFILES = 32
MAX_RANK = 100
MAX_POLICY_BYTES = 65536
MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,199}$")
EFFORT_ORDER = {name: rank for rank, name in enumerate(("none", "minimal", "low", "medium", "high", "xhigh", "max"))}


def _rank(value, label):
    if type(value) is not int or not 1 <= value <= MAX_RANK:
        raise LoopError(f"{label} must be an integer between 1 and {MAX_RANK}")
    return value


def validate_policy(value):
    """Return a detached, deterministically ordered policy; never infer defaults."""
    if not isinstance(value, dict) or set(value) != POLICY_FIELDS or type(value.get("version")) is not int or value["version"] != 1:
        raise LoopError("Routing policy needs version 1, profiles and requirements")
    requirements = value["requirements"]
    if not isinstance(requirements, dict) or set(requirements) != set(LEVELS):
        raise LoopError("Routing requirements need exactly low, medium and high capability ranks")
    requirements = {level: _rank(requirements[level], "Routing requirement " + level) for level in LEVELS}
    if list(requirements.values()) != sorted(requirements.values()):
        raise LoopError("Routing requirements must satisfy low <= medium <= high")
    rows = value["profiles"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PROFILES:
        raise LoopError(f"Routing needs between one and {MAX_PROFILES} explicit profiles")
    profiles, ids, choices = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != PROFILE_FIELDS or not identifier(row.get("id")):
            raise LoopError("Each routing profile needs exactly the documented fields and a valid ID")
        if row["provider"] not in ("claude", "codex"):
            raise LoopError("Routing profile provider must be claude or codex")
        model = row["model"]
        if (not isinstance(model, str) or not MODEL.fullmatch(model)
                or model.casefold() in ("auto", "default", "latest", "inherit", "ultra", "ultracode", "opusplan")
                or model.casefold().endswith("-latest")):
            raise LoopError("Routing profiles need a concrete explicit model ID, not an automatic/default alias")
        if row["effort"] is None:
            raise LoopError("Routing profiles need an explicit model-supported effort")
        effort_args(row["provider"], row["effort"])
        if row["effort"] in ("inherit", "latest", "ultra", "ultracode"):
            raise LoopError("Routing profiles need an explicit model-supported effort")
        profile = {"id": row["id"], "provider": row["provider"], "model": model, "effort": row["effort"],
                   "capability_rank": _rank(row["capability_rank"], "Profile capability_rank"),
                   "cost_rank": _rank(row["cost_rank"], "Profile cost_rank")}
        choice = (profile["provider"], model, profile["effort"])
        if profile["id"] in ids:
            raise LoopError("Routing profile IDs must be unique")
        if choice in choices:
            raise LoopError("Duplicate provider/model/effort profiles cannot represent an escalation")
        ids.add(profile["id"])
        choices.add(choice)
        profiles.append(profile)
    for previous in profiles:
        for following in profiles:
            if ((previous["provider"], previous["model"]) == (following["provider"], following["model"])
                    and following["capability_rank"] > previous["capability_rank"]
                    and previous["effort"] in EFFORT_ORDER and following["effort"] in EFFORT_ORDER
                    and EFFORT_ORDER[following["effort"]] < EFFORT_ORDER[previous["effort"]]):
                raise LoopError("A higher capability rank cannot lower recognized effort on the same provider/model")
    return {"version": 1, "profiles": sorted(profiles, key=lambda item: item["id"]), "requirements": requirements}


def load_policy(path=None, environ=None):
    """Import an explicit policy during goal setting, without mutating the file.

    Missing configuration is a blocker. Authentication and model support still
    need provider preflight; valid JSON does not establish either.
    """
    environment = os.environ if environ is None else environ
    source = path if path is not None else environment.get("LOOP_ROUTING_PROFILE")
    if not isinstance(source, (str, os.PathLike)) or not str(source).strip():
        raise LoopError("Configure an explicit routing policy before work with LOOP_ROUTING_PROFILE or a profile file")
    try:
        source = Path(source).expanduser().resolve()
        if not source.is_file() or source.stat().st_size > MAX_POLICY_BYTES:
            raise LoopError("Routing policy must be a readable JSON file no larger than 64 KiB")
        return validate_policy(read_json(source))
    except (OSError, ValueError) as exc:
        raise LoopError("Cannot read routing policy: " + str(exc)) from exc


def policy_seal(policy):
    return digest(validate_policy(policy))


def _planning_fields(planning):
    if not isinstance(planning, dict) or set(planning) != PLANNING_FIELDS:
        raise LoopError("Assignment planning needs exactly the documented goal, boundary and demand fields")
    refs = planning["goal_criteria"]
    if (not isinstance(refs, list) or not 1 <= len(refs) <= 100
            or not all(identifier(item) for item in refs) or len(refs) != len(set(refs))):
        raise LoopError("Planning goal_criteria needs distinct parent criterion IDs")
    normalized = {"goal_criteria": sorted(refs),
                  "rationale": concise(planning["rationale"], "Assignment boundary rationale"),
                  "verification": concise(planning["verification"], "Assignment verification")}
    for dimension in ("complexity", "risk", "uncertainty"):
        if planning[dimension] not in LEVELS:
            raise LoopError(f"Planning {dimension} must be low, medium or high")
        normalized[dimension] = planning[dimension]
    evidence = planning["evidence"]
    if not isinstance(evidence, list) or len(evidence) > 5:
        raise LoopError("Planning evidence needs at most five specific references")
    evidence = [concise(item, "Planning evidence reference", 1000) for item in evidence]
    if len(evidence) != len(set(evidence)):
        raise LoopError("Planning evidence must not repeat references")
    if any(normalized[key] != "low" for key in ("complexity", "risk", "uncertainty")) and not evidence:
        raise LoopError("Higher starting demand requires factual planning evidence")
    normalized["evidence"] = evidence
    return normalized


def validate_planning(planning, goal, phase):
    """Check goal traceability without allowing a worker to redefine the goal."""
    normalized = _planning_fields(planning)
    if not isinstance(goal, dict):
        raise LoopError("Planning needs the current parent goal")
    refs = set(normalized["goal_criteria"])
    if PREFLIGHT_CRITERION in refs:
        if phase not in ("draft", "goal_setting") or refs != {PREFLIGHT_CRITERION}:
            raise LoopError("The goal_setting criterion is only for preflight and cannot mix with accepted criteria")
        return normalized
    target = goal.get("target")
    rows = target.get("acceptance") if isinstance(target, dict) else goal.get("criteria")
    if not isinstance(rows, list):
        raise LoopError("Planning needs actual parent acceptance/completion criteria")
    known = {row.get("id") for row in rows if isinstance(row, dict) and identifier(row.get("id"))}
    if not refs <= known:
        raise LoopError("Planning references unknown parent criteria: " + ", ".join(sorted(refs - known)))
    return normalized


def _provider_profiles(policy, provider):
    if provider not in ("claude", "codex"):
        raise LoopError("Select a known worker provider before routing")
    profiles = [item for item in policy["profiles"] if item["provider"] == provider]
    if not profiles:
        raise LoopError("No explicit routing profile is configured for worker provider " + provider)
    return profiles


def _economical(profiles):
    return min(profiles, key=lambda item: (item["cost_rank"], item["capability_rank"], item["id"]))


def select_profile(policy, provider, planning):
    """Choose the cheapest configured profile meeting all three demand dimensions."""
    policy = validate_policy(policy)
    planning = _planning_fields(planning)
    profiles = _provider_profiles(policy, provider)
    required = max(policy["requirements"][planning[key]] for key in ("complexity", "risk", "uncertainty"))
    eligible = [item for item in profiles if item["capability_rank"] >= required]
    if not eligible:
        raise LoopError("No configured worker profile meets the assignment demand; resolve routing before dispatch")
    selected = _economical(eligible)
    if selected["capability_rank"] > min(item["capability_rank"] for item in profiles) and not planning["evidence"]:
        raise LoopError("Starting above the smallest configured capability requires factual planning evidence")
    return selected


def escalate_profile(policy, provider, current_profile_id):
    """Select a strictly stronger, different model/effort after a quality rejection.

    The runtime must establish and persist the rejection before calling this pure
    selector. This function grants no new attempt or call budget.
    """
    policy = validate_policy(policy)
    profiles = _provider_profiles(policy, provider)
    current = next((item for item in profiles if item["id"] == current_profile_id), None)
    if current is None:
        raise LoopError("The prior worker profile is not in the frozen provider policy")
    eligible = [item for item in profiles if item["capability_rank"] > current["capability_rank"]
                and (item["model"], item["effort"]) != (current["model"], current["effort"])
                and (item["model"] != current["model"] or current["effort"] not in EFFORT_ORDER
                     or item["effort"] not in EFFORT_ORDER
                     or EFFORT_ORDER[item["effort"]] > EFFORT_ORDER[current["effort"]])]
    if not eligible:
        raise LoopError("No higher allowed worker profile remains; preserve the rejected result and finite budgets")
    return _economical(eligible)


def _normalized_text(value):
    return " ".join(value.casefold().split()).rstrip(".。")


def validate_splits(assignments, goal, phase):
    """Validate structural work boundaries; semantic necessity remains a main check."""
    if not isinstance(assignments, list) or not 2 <= len(assignments) <= 12:
        raise LoopError("A team needs two to twelve distinct assignments; use delegate for one bounded unit")
    signatures, writers, ids = set(), {}, set()
    for assignment in assignments:
        if not isinstance(assignment, dict) or not identifier(assignment.get("id")):
            raise LoopError("Split assignments need valid IDs")
        if assignment["id"] in ids:
            raise LoopError("Split assignment IDs must be unique")
        ids.add(assignment["id"])
        planning = validate_planning(assignment.get("planning"), goal, phase)
        descriptions = {}
        for field in ("deliverables", "criteria"):
            rows = assignment.get(field)
            if (not isinstance(rows, list) or not rows or not all(isinstance(row, dict)
                    and isinstance(row.get("description"), str) and row["description"].strip() for row in rows)):
                raise LoopError("Every split needs independently verifiable deliverables and completion criteria")
            descriptions[field] = sorted(_normalized_text(row["description"]) for row in rows)
        paths = {}
        for field in ("read_paths", "write_paths"):
            values = assignment.get(field)
            if (not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values)
                    or len(values) != len(set(values))):
                raise LoopError("Split read/write scopes need distinct explicit paths")
            paths[field] = sorted(values)
        for name in paths["write_paths"]:
            if name in writers:
                raise LoopError("Team write ownership overlaps for " + name + ": " + writers[name] + " and " + assignment["id"])
            writers[name] = assignment["id"]
        # IDs, wording of the instruction and ranking justifications do not turn
        # the same promised work into a separate contribution.
        signature = digest({"kind": assignment.get("kind"), "paths": paths,
                            "goal_criteria": planning["goal_criteria"], **descriptions})
        if signature in signatures:
            raise LoopError("Duplicate assignment contribution; combine the work instead of splitting it again")
        signatures.add(signature)
