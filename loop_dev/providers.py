"""Cross-provider, read-only, structured review adapters."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from .process import execute
from .storage import LoopError, read_json, write_json


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
REVIEW_SCHEMA = obj({
    "verdict": {"type": "string", "enum": ["pass", "revise", "blocked"]},
    "summary": STRING,
    "findings": {"type": "array", "items": obj({
        "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
        "target": STRING, "evidence": STRING, "remedy": STRING})},
    "questions": {"type": "array", "items": STRING},
    "coverage": {"type": "array", "items": obj({
        "acceptance_id": STRING, "satisfied": {"type": "boolean"}, "evidence": STRING})},
})


def reviewer(implementer):
    if implementer not in ("claude", "codex"):
        raise LoopError("Unknown implementer")
    return "codex" if implementer == "claude" else "claude"


def command(provider, root, directory):
    executable = shutil.which(provider)
    if not executable:
        raise LoopError(f"Required CLI is not installed: {provider}")
    schema_path = directory / "schema.json"
    if provider == "codex":
        return [executable, "--ask-for-approval", "never", "--sandbox", "read-only",
                "exec", "--ephemeral", "--output-schema", str(schema_path),
                "--output-last-message", str(directory / "answer.json"), "-"]
    if provider == "claude":
        return [executable, "-p", "--output-format", "json", "--json-schema",
                json.dumps(REVIEW_SCHEMA, separators=(",", ":")),
                "--permission-mode", "dontAsk", "--tools", "Read,Glob,Grep",
                "--allowedTools", "Read,Glob,Grep", "--disallowedTools", "AskUserQuestion,mcp__*",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence"]
    raise LoopError(f"Unknown provider: {provider}")


def doctor(root, directory):
    reports = {}
    for provider in ("claude", "codex"):
        executable = shutil.which(provider)
        if not executable:
            reports[provider] = {"ok": False, "error": "CLI not found on PATH"}
            continue
        result = execute([executable, "--version"], root, directory / provider, 15)
        reports[provider] = {"ok": result["exit_code"] == 0 and not result["timed_out"],
                             "executable": executable, "process": result,
                             "version": Path(result["stdout"]).read_text(errors="replace").strip()[:200]}
    return {"ok": all(x["ok"] for x in reports.values()), "providers": reports,
            "note": "Version checks do not prove authentication. plan-review is the live reviewer preflight."}


def validate_review(value, expected_ids, phase):
    def string(x):
        return isinstance(x, str) and bool(x.strip())
    if not isinstance(value, dict) or set(value) != set(REVIEW_SCHEMA["properties"]):
        raise LoopError("Reviewer returned an invalid object")
    if value["verdict"] not in ("pass", "revise", "blocked") or not string(value["summary"]):
        raise LoopError("Reviewer returned an invalid verdict or summary")
    if not isinstance(value["questions"], list) or not all(string(x) for x in value["questions"]):
        raise LoopError("Reviewer questions must be strings")
    if phase != "plan" and value["questions"]:
        raise LoopError("Execution reviewer tried to ask questions; resolve using frozen decisions or report blocked")
    if not isinstance(value["findings"], list):
        raise LoopError("Reviewer findings must be an array")
    for item in value["findings"]:
        if (not isinstance(item, dict) or set(item) != {"severity", "target", "evidence", "remedy"}
                or item["severity"] not in ("blocker", "major", "minor")
                or not all(string(item[x]) for x in ("target", "evidence", "remedy"))):
            raise LoopError("Reviewer finding is incomplete")
    if not isinstance(value["coverage"], list):
        raise LoopError("Reviewer coverage must be an array")
    ids = []
    for item in value["coverage"]:
        if (not isinstance(item, dict) or set(item) != {"acceptance_id", "satisfied", "evidence"}
                or not string(item["acceptance_id"]) or type(item["satisfied"]) is not bool
                or not string(item["evidence"])):
            raise LoopError("Reviewer coverage is incomplete")
        ids.append(item["acceptance_id"])
    if len(ids) != len(set(ids)) or set(ids) != set(expected_ids):
        raise LoopError("Reviewer must cover every requested acceptance ID exactly once")
    passed = (value["verdict"] == "pass" and not value["findings"] and not value["questions"]
              and all(x["satisfied"] for x in value["coverage"]))
    if value["verdict"] == "pass" and not passed:
        raise LoopError("Reviewer claimed pass with unresolved findings, questions, or unmet acceptance")
    return passed


def review(provider, root, directory, prompt, timeout, expected_ids, phase):
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "schema.json", REVIEW_SCHEMA)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    result = execute(command(provider, root, directory), root, directory, timeout, prompt=prompt)
    write_json(directory / "process.json", result)
    if result["exit_code"] != 0 or result["timed_out"]:
        raise LoopError(f"{provider} review failed; inspect {directory / 'stderr.log'}")
    source = directory / ("answer.json" if provider == "codex" else "stdout.log")
    if not source.exists() or source.stat().st_size > 1024 * 1024:
        raise LoopError("Missing or oversized reviewer result")
    value = read_json(source)
    if provider == "claude":
        if not isinstance(value, dict) or value.get("is_error") or value.get("permission_denials"):
            raise LoopError("Claude review failed or reported denied permissions")
        value = value.get("structured_output")
    passed = validate_review(value, expected_ids, phase)
    write_json(directory / "review.json", value)
    return {"provider": provider, "passed": passed, "result": value,
            "artifact": str(directory / "review.json")}
