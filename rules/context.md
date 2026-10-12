# Context is work the loop owns

Before asking the user anything, classify the missing context:

| Missing item | Loop responsibility |
| --- | --- |
| Where behavior lives | Search source, trace entry points/callers and tests |
| Current implementation maturity | Record evidence and gap for every requirement |
| Existing conventions | Read root/nested rules and comparable implementations |
| How to build/test | Inspect manifests, CI and documented commands; verify prerequisites |
| A failed test or review | Read logs, reproduce, identify root cause and repair |
| External library/API details | Read authoritative docs and supported local versions |
| A dependency discovered mid-task | Add evidence and adjust tactics within fixed scope |
| Business behavior or material scope ambiguity | Ask before start, with recommended choice |
| A previously decided matter | Read the frozen contract and notes; never ask again |
| Lost/compacted conversation | Rebuild from `context`, gate artifacts and source |

The generated context contains repository paths, manifest excerpts, rules, target,
gaps, milestones, latest gate and discoveries. It is an index, not proof that every
source file has been understood. Follow the relevant paths and cite concrete
evidence. Never treat a filename alone as evidence of a complete feature.

Tactical replanning is automatic and continuous: assess the latest evidence, keep
the original acceptance criteria, choose the next smallest complete change, inspect
its impact, implement, then validate. If a change affects completed milestones,
re-run relevant checks immediately; the final gate always rechecks the full target.

Do not write secrets into goals, decisions, notes or logs. Reference credential
names and setup requirements. Local logs are ignored by Git, but still contain
project context; share them only intentionally.
