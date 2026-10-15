# Writing for people and agents

Write the conclusion first, then the evidence needed to use it. Match the user's
language and technical level. Preserve precise terms, constraints and uncertainty;
explain an unfamiliar term briefly when needed. Simplicity must not change the facts.

## Record shape

| Record | Required content | Optional content |
| --- | --- | --- |
| Execution status | goal, actual stage, result/evidence, next agent action or blocker | latest material finding |
| Knowledge topic | one conclusion, scope, status, source references, source dependencies | why it matters, up to five distinct details, uncertainty, decision boundary |
| Completed work | delivered result, output reference, each criterion and its evidence | new supported knowledge |

Use `summary` for the conclusion; `why` only for a useful implication; `details` for
distinct supporting facts; `uncertainty` for an actual limit. Omit unused fields rather
than writing "none", boilerplate, or a second summary. Keep each authored summary,
finding and optional topic point on one line, at most 280 characters. Task titles are
at most 120; common goal outcome/deliverable fields at most 400. Put a longer explanation
in the requested deliverable and link it. Never cut evidence to fit a summary.

## Before saving or reporting

1. State what changed or what the evidence establishes. Delete intent announcements,
   praise, repeated recaps, and closers such as "let me know if you need anything".
2. Use one idea per sentence. Name the affected behavior and cause. Avoid abstract
   claims such as "improved robustness" without the actual observable change.
3. Keep source paths, commands and results exact. Distinguish fact, decision, inference
   and unknown; do not replace uncertainty with confidence or invent time estimates.
4. Show at most five items per visible group, with a count and route to the rest.
   Order actual steps; use a table for comparisons. Preserve all underlying evidence.
5. Give the agent's next action only while work remains. Do not turn it into a new
   instruction for the user unless a real user-owned choice is pending before start.

Use an analogy only if it clarifies an unfamiliar mechanism. Do not force childlike
language, medical assumptions, emojis, a fixed recap, or an unnecessary question into
every output. Explanations may be as long as the requested understanding requires;
the bounded fields are navigation records, not a limit on the deliverable itself.

## Storage and maintenance

SQLite and development state JSON are authoritative records. Markdown views are
generated deterministically from them, without a second model-written paraphrase.
Update the same topic ID when evidence changes. Do not create daily narrative copies,
repeat task history inside every topic, or save an unchanged fact as a new revision.
If nothing was learned, the task result is enough. Keep obsolete versions in history.

`context` refreshes applicability from dependencies; it never verifies a fact just by
reading it. A topic document's confirmed label is not a freshness guarantee: use the
current context view. `knowledge get` and `work context` regenerate derived views if
needed. Do not hand-edit generated Markdown to change task status or facts.

The scripts reject blank/placeholder-only, oversized or multiline summaries, repeated
topic fields and duplicate evidence references. These checks prevent common bloat;
the agent still checks factual accuracy and useful wording. A word blacklist cannot
establish writing quality, and readability is not another review loop.

## References

Adapted principles, not installed hooks or runtime dependencies:

- [ELI5](https://github.com/DreambigOu/ELI5/blob/main/skills/eli5/SKILL.md): audience-appropriate vocabulary, purpose before mechanism, progressive explanation. Read blob `b0644b6cbc4839749aeecf0b340074be9e88f475`.
- [i-have-adhd](https://github.com/ayghri/i-have-adhd/blob/main/skills/i-have-adhd/SKILL.md): visible state/action, small groups, no tangents or repeated recap. Read blob `6c42757ffae37e442adfb52fbba2a8609f1cff00`.

The harness retains exact evidence, its question policy and the user's requested
depth. It does not adopt a five-year-old default audience or unsupported estimates.
