"""Read-only CLI transports for bounded workers and independent reviews."""

from __future__ import annotations

import json
from pathlib import Path
import re
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


def effort_args(provider, effort):
    if effort is None:
        return []
    if not isinstance(effort, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', effort) or effort in ('auto', 'default'):
        raise LoopError('Use an explicit effort level supported by the selected model; no automatic fallback')
    if provider == 'codex':
        return ['-c', 'model_reasoning_effort=' + json.dumps(effort),
                '-c', 'plan_mode_reasoning_effort=' + json.dumps(effort)]
    if provider == 'claude':
        return ['--effort', effort]
    raise LoopError('Unknown effort provider')


def command(provider, root, directory, schema=None, outside_git=False, model=None, effort=None):
    executable = shutil.which(provider)
    if not executable:
        raise LoopError(f"Required CLI is not installed: {provider}")
    schema_path = directory / "schema.json"
    if provider == "codex":
        return [executable, "--ask-for-approval", "never", "--sandbox", "read-only",
                "exec", *(["--model", model] if model else []), *effort_args(provider, effort), *(["--skip-git-repo-check"] if outside_git else []), "--ephemeral", "--output-schema", str(schema_path),
                "--output-last-message", str(directory / "answer.json"), "-"]
    if provider == "claude":
        return [executable, "-p", *(["--model", model] if model else []), *effort_args(provider, effort), "--output-format", "json", "--json-schema",
                json.dumps(schema or REVIEW_SCHEMA, separators=(",", ":")),
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


def structured_call(provider, root, directory, prompt, timeout, schema, outside_git=False, model=None, effort=None):
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "schema.json", schema)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    env = {'CLAUDE_CODE_EFFORT_LEVEL': effort} if provider == 'claude' and effort else None
    result = execute(command(provider, root, directory, schema, outside_git, model, effort), root, directory, timeout,
                     prompt=prompt, env_overrides=env)
    write_json(directory / "process.json", result)
    if result["exit_code"] != 0 or result["timed_out"]:
        raise LoopError(f"{provider} call failed; inspect {directory / 'stderr.log'}")
    source = directory / ("answer.json" if provider == "codex" else "stdout.log")
    if not source.exists() or source.stat().st_size > 1024 * 1024:
        raise LoopError("Missing or oversized worker/reviewer result")
    value = read_json(source)
    if provider == "claude":
        if not isinstance(value, dict) or value.get("is_error") or value.get("permission_denials"):
            raise LoopError("Claude call failed or reported denied permissions")
        value = value.get("structured_output")
    return value


def review(provider, root, directory, prompt, timeout, expected_ids, phase):
    value = structured_call(provider, root, directory, prompt, timeout, REVIEW_SCHEMA)
    passed = validate_review(value, expected_ids, phase)
    write_json(directory / "review.json", value)
    return {"provider": provider, "passed": passed, "result": value,
            "artifact": str(directory / "review.json")}
