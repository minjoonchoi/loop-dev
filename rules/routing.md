# Split by outcomes; route by demand

The main session owns work boundaries and checks returned evidence. Use
`delegation.md` for execution and assessment. Configure available worker profiles
during goal setting; never inherit an unspecified CLI model or effort at dispatch.

## Define the smallest complete unit

Keep a small, cohesive task in one `delegate` call. Use a team only when each member
contributes a distinct, independently verifiable part of the goal. Separate work by
output, source boundary or a real dependency, not by decorative role names or individual
editing steps. Account for handoff cost before splitting. A team has 2–12 members;
each writable file has one owner, dependencies are explicit and acyclic, and duplicate
contributions are rejected. The main session still checks whether a valid split is useful.

Every assignment adds this `planning` object to the contract in `delegation.md`:

| Field | Required content |
| --- | --- |
| `goal_criteria` | Distinct IDs of the parent's acceptance/completion criteria |
| `rationale` | Why this scope is one complete, useful unit; one line, at most 280 characters |
| `verification` | How its contribution will be checked; one line, at most 280 characters |
| `complexity` | `low`, `medium` or `high`: interacting behavior and dependencies |
| `risk` | `low`, `medium` or `high`: consequence of an incorrect result |
| `uncertainty` | `low`, `medium` or `high`: unresolved technical evidence |
| `evidence` | Up to five factual references supporting the demand, at most 1,000 characters each |

Only preflight may use the sole criterion `goal_setting` while defining the target.
It cannot be mixed with accepted criterion IDs or used after start. Any medium/high
dimension needs evidence. Prefer a bounded low-demand investigation when uncertainty
can be resolved cheaply; do not label every task high to avoid deciding its scope.

## Freeze the available profiles

Store the policy under the goal's `execution.routing`. The optional
`LOOP_ROUTING_PROFILE=/absolute/profile.json` imports the same policy during goal
setting, before the goal is sealed. Subsequent environment changes do not replace
the frozen policy. Missing or unsupported configuration blocks dispatch.

| Policy field | Contract |
| --- | --- |
| `version` | `1` |
| `profiles` | 1–32 entries with unique IDs and unique provider/model/effort combinations |
| `requirements` | Exactly `low`, `medium`, `high`; minimum capability ranks with `low <= medium <= high` |

Each profile has exactly `id`, `provider` (`claude` or `codex`), `model`, `effort`,
`capability_rank` and `cost_rank`. Ranks and requirement values are integers from
1 to 100. Models and efforts must be explicit supported values; automatic/default
aliases are not fallback choices. These ranks are local configuration, not measured
model capability, prices or interchangeable effort levels between providers.
Use concrete IDs available to the account and verify CLI support before execution.

The runtime takes the highest required capability across the three demand dimensions,
then selects the eligible profile with lowest `cost_rank`, then `capability_rank`,
then `id`. Selection stays within the task's worker provider. Starting above that
provider's smallest configured capability also requires factual planning evidence.
Do not start all work on the largest model or highest effort. A small task normally
starts on the least expensive eligible profile; a consequential task can justify a
higher starting requirement without first producing a knowingly inadequate result.

Development's independent reviewer remains on the opposite provider with an explicit,
suitable profile. Its demand is medium by default; recorded high risk or uncertainty
with concrete assignment evidence raises it to high. File or criterion counts alone
do not justify that increase. Select the least expensive eligible review profile;
missing capability blocks review. Review requirements are not lowered to match a cheap worker.
Requested model/effort and profile provenance are recorded. Managed host policy may
cap effort; do not claim effective settings, exact cost or savings without observations.
Claude calls constrain fallback to the selected model and disable per-call switch prompts
(`switchModelsOnFlag: false`) without changing host files or overriding managed policy.
Provider configuration references: [Codex](https://developers.openai.com/codex/config-reference/)
and [Claude Code](https://code.claude.com/docs/en/model-config).

## Escalate only a rejected result

Workers return staged candidates. The main session assesses their goal contribution
through `assess`; this is an internal evidence check, not a user approval or a mandatory
extra model call. Acceptance enables the candidate's effects. Rejection quarantines
the whole candidate: proposed edits, findings, outputs and peer messages cannot become
working facts or prerequisite results. Retain its audit evidence and pass only the
bounded failure reason to the next attempt.

After a recorded quality rejection, rerun the same assignment/team. The runtime picks
the least expensive allowed profile with strictly higher capability and a different
model or effort, on the same provider. For a recognized effort order, a higher capability
rank on the same model cannot lower effort. A different model may use lower effort
with a higher configured rank; the rank still does not prove measured capability.
Immutable scope, the three-attempt limit and
shared call/time budgets remain in force. No stronger profile or remaining budget
means an honest blocker; new IDs must not reset either limit.

Authentication, permission, unavailable models, timeout, changed source/feedback and
ambiguous authority are blockers, not quality evidence for escalation. Resolve them
within the existing scope or preserve the blocker. Never buy a larger model to bypass
a missing permission, silently broaden the goal, or ask new questions after work starts.
