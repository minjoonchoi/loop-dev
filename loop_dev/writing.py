"""Small, factual records and deterministic human views of the same data."""

from __future__ import annotations

import re

from .storage import LoopError


def concise(value, field, maximum=280):
    if not isinstance(value, str) or not value.strip():
        raise LoopError(f"{field} needs a concrete statement")
    if "\n" in value or "\r" in value or len(value) > maximum:
        raise LoopError(f"{field} must be one line, at most {maximum} characters; link details separately")
    if (re.fullmatch(r"(?:TODO|TBD|FIXME|\.\.\.|placeholder|작성 예정|추후 작성)[.!?]?", value.strip(), re.I)
            or re.match(r"Replace with\b", value.strip(), re.I)):
        raise LoopError(f"{field} must replace its placeholder with the observed result")
    return value.strip()


def topic_text(entry):
    concise(entry.get("summary"), "Knowledge summary")
    for key in ("why", "uncertainty"):
        if key in entry:
            concise(entry[key], key)
    details = entry.get("details", [])
    if not isinstance(details, list) or len(details) > 5:
        raise LoopError("Knowledge details must contain at most five points; link longer material")
    texts = [entry["summary"], *[concise(x, "Knowledge detail") for x in details]]
    for key in ("why", "uncertainty"):
        if key in entry:
            texts.append(entry[key])
    normalized = [" ".join(x.casefold().split()).rstrip(".。") for x in texts]
    if len(set(normalized)) != len(normalized):
        raise LoopError("Knowledge fields repeat the same statement; keep it once")
    evidence = entry.get("evidence", [])
    if isinstance(evidence, list) and all(isinstance(x, str) for x in evidence) and len(set(evidence)) != len(evidence):
        raise LoopError("Knowledge evidence repeats a reference")


def plain(value):
    """Prevent source text from introducing headings, links or HTML in generated views."""
    value = " ".join(str(value).split()).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", value)


def bullets(items, maximum=5):
    lines = ["- " + plain(item) for item in items[:maximum]]
    if len(items) > maximum:
        lines.append(f"- +{len(items) - maximum} more in the linked JSON record.")
    return lines


def task_summary(task):
    result = task.get("result") or {}
    return {**{k: task.get(k) for k in ("id", "kind", "title", "phase", "session_id", "updated_at")},
            "result": {k: result[k] for k in ("summary", "gate", "historical", "evidence_current") if k in result}}


def task_document(task):
    goal = task.get("goal") or {}
    result = task.get("result") or {}
    development = task["kind"] == "development"
    outcome = goal.get("target", {}).get("outcome") if development else goal.get("outcome")
    lead = result.get("summary") or outcome or task["title"]
    phase = task["phase"]
    if result.get("evidence_current") is False:
        lead = "Earlier completion is stale: the source or rules changed."
    elif phase == "blocked" and task.get("reason"):
        lead = "Work is blocked: " + task["reason"]
    labels = {"goal_setting": "Setting the goal", "working": "In progress", "completed": "Completed",
              "blocked": "Blocked", "aborted": "Stopped"}
    lines = [f"# {plain(task['title'])}", ""]
    if lead != task["title"]:
        lines.extend([plain(lead), ""])
    lines.append(f"**State:** {labels.get(phase, plain(phase))} · **Kind:** {plain(task['kind'])}")
    if outcome and outcome != lead:
        lines.extend(["", "**Goal:** " + plain(outcome)])
    if development:
        lines.extend(["", "**Development stage:** " + plain(task["execution_phase"])])
        if task.get("progress"):
            lines.extend(["", "**Progress:** " + plain(task["progress"])])
    if result.get("evidence_current") is False:
        lines.extend(["", "**Evidence:** historical; run fresh verification before claiming current completion."])
    if phase == "completed":
        lines.extend(["", "**Result:** recorded completion; a later request must check current evidence."])
    elif task.get("next_action"):
        lines.extend(["", "**Next (agent):** " + plain(task["next_action"])])
    else:
        next_steps = {"goal_setting": "Resolve remaining choices and define completion criteria.",
                      "working": "Delegate the scoped work, inspect its deliverable and check completion criteria.",
                      "blocked": "Resolve the recorded obstacle within the agreed authority, then resume."}
        if phase in next_steps:
            lines.extend(["", "**Next (agent):** " + next_steps[phase]])
    checks = result.get("checks") or (task.get("last_result") or {}).get("checks", [])
    if checks:
        lines.extend(["", "## Checks", "", *bullets([
            f"{c['criterion_id']}: {'passed' if c['satisfied'] else 'unmet'} — {c['evidence']}" for c in checks])])
    refs = result.get("outputs", []) + ([result["gate"]] if result.get("gate") else [])
    if refs:
        lines.extend(["", "## Evidence and output", "", *bullets(refs)])
    if task.get("delegations"):
        lines.extend(["", "## Delegated work", "", *bullets([
            f"{x['assignment_id']}: {x['status']} — {x['error'] or x['summary']} ({x['id']})"
            for x in task["delegations"]])])
    if task.get("notes"):
        note = task["notes"][-1]
        lines.extend(["", "**Latest finding:** " + plain(note.get("text", "")),
                      "", "**Source:** " + plain(note.get("evidence", ""))])
    lines.extend(["", f"[Full record]({task['id']}.json)", ""])
    return "\n".join(lines)


def delegation_document(record):
    request = record["request"]
    lines = [f"# {plain(request['id'])}", "", plain(record["summary"]), "",
             f"**State:** {plain(record['status'])} · **Worker:** {plain(record['provider'])} · **Attempt:** {record['attempts']}",
             "", "**Assignment:** " + plain(request["instruction"])]
    lines.extend(["", "**Working directory:** " + plain(record["root"])])
    for title, items in (("Read scope", request["read_paths"]), ("Allowed edits", request["write_paths"]),
                         ("Prohibited", request["prohibited"]),
                         ("Deliverables", [x["id"] + ": " + x["description"] for x in request["deliverables"]]),
                         ("Completion criteria", [x["id"] + ": " + x["description"] for x in request["criteria"]]),
                         ("Applied edits", record["applied"])):
        lines.extend(["", "## " + title, "", *(bullets(items) if items else ["None."])])
    if record.get("error"):
        lines.extend(["", "**Obstacle:** " + plain(record["error"])])
    lines.extend(["", "**Evidence directory:** " + plain(record["directory"]), "",
                  "Done applies to this assignment only; parent completion uses its own criteria and gates.", "",
                  f"[Full record]({record['id']}.json)", ""])
    return "\n".join(lines)


def topic_document(topic):
    lines = [f"# {plain(topic['id'])}", "", plain(topic["summary"]), "",
             f"**Status:** {plain(topic['status'])} · **Scope:** {plain(topic['scope'])} · **Kind:** {plain(topic['kind'])}"]
    for label, key in (("Why it matters", "why"), ("Uncertainty", "uncertainty"), ("Applies to", "applies_to"),
                       ("Decision basis", "basis")):
        if topic.get(key):
            lines.extend(["", f"**{label}:** " + plain(topic[key])])
    if topic.get("details"):
        lines.extend(["", *bullets(topic["details"])])
    if topic.get("evidence"):
        lines.extend(["", "## Evidence", "", *bullets(topic["evidence"])])
    lines.extend(["", "**Recorded:** " + plain(topic["recorded_at"]), "",
                  "Current applicability is computed by knowledge context; confirmed alone does not mean current.", "",
                  f"[Full record]({topic['id']}.json)", ""])
    return "\n".join(lines)


def context_document(context):
    counts = context["counts"]
    lines = ["# Local knowledge", "",
             f"{counts['reusable']} current · {counts['stale']} need rechecking · {counts['unconfirmed']} unconfirmed", "",
             "Use current evidence within its recorded scope. Past results do not verify today's files."]
    activation = context.get("harness")
    if activation:
        state = "on · all inputs in this conversation" if activation["enabled"] else "off"
        lines.extend(["", f"**Harness:** {state} · session `{plain(activation['session_id'])}`"])
    for key, label in (("reusable", "Current"), ("stale", "Recheck before reuse"),
                       ("unconfirmed", "Drafts and hypotheses"), ("tasks", "Work history")):
        values = context[key]
        if not values:
            continue
        lines.extend(["", "## " + label, ""])
        folder = "task" if key == "tasks" else "topic"
        for item in values[:5]:
            summary = item.get("summary", item.get("title", ""))
            reason = "; ".join(item.get("stale_reasons", []))
            state = item.get("phase", item.get("status", ""))
            lines.append(f"- [{plain(item['id'])}](../{folder}/{item['id']}.md): {plain(summary)} ({plain(reason or state)})")
        remaining = counts[key] - min(len(values), 5)
        if remaining:
            lines.append(f"- {remaining} records outside this view; use knowledge context --query or --offset. Nothing is deleted.")
    lines.extend(["", f"**Revision:** {context['revision']}", "", "[Structured view](./" + context["view_id"] + ".json)", ""])
    return "\n".join(lines)
