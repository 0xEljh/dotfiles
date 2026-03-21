## Design Decisions (IMPORTANT)

- You may propose and endorse design decisions, solutions, and design patterns as you deem fit, with adequate justification
- **MANDATORY**: When justifying a decision, state alternatives and their pros, cons, and trade-offs so that a "real choice" was made.
- Avoid over-engineering; keep solutions focused (which often means simple and to have a balanced approach to coupling and dependency surface).

If this was asked during a planning phase or discussion with the user, you should provide 2-3 design/implementation approaches wherever applicable. This is NOT optional.

For each approach:

```

APPROACH A: [Name]
  Summary: [1-2 sentences]
  Effort:  [S/M/L/XL]
  Risk:    [Low/Med/High]
  Pros:    [2-3 bullets]
  Cons:    [2-3 bullets]
  Reuses:  [existing code/patterns leveraged]

APPROACH B: [Name]
  ...

APPROACH C: [Name] (optional — include if a meaningfully different path exists)
  ...
```

Rules:

- At least 2 approaches required. 3 preferred for non-trivial designs.
- One must be the **"minimal viable"** (fewest files, smallest diff, ships fastest).
- One must be the **"ideal architecture"** (best long-term trajectory, most elegant).
- One can be **creative/lateral** (unexpected approach, different framing of the problem).

## Design Documents

- Cross reference online documentation and codebases while planning or writing design documents.
- Include links to the relevant documentation and code snippets in these plans/docs. Plans may sometimes be handed off to an engineering team for review and implementation.
- When a plan should be persisted, write it as a `.md` file in `docs/designs/` (prefer `/design` when available) so it is easy to review, edit, and reuse across sessions.

## Test Driven Development (TDD)

If the codebase has tests, then TDD should be adopted. That is, tests should be written first, then code.
Only as a final step do we wrangle tests and code. This reduces "empty tests" and also limits spec drift.

During planning, the tests to be implemented should already be defined based on the plan's specs.
At the point of implementation, the tests should fail. Tests that don't initially fail are not useful and should be removed.

## Maximise exploration/search during planning

MAXIMISE SEARCH EFFORTS. Launch multiple background agents in parallel.
Look up codebase patterns, file structures, ripgrep (rg)
Check remote repos, official docs, GitHub examples.
Search up best practices, design considerations, and reference implementations.
NEVER stop at the first result - be exhaustive.

## Agent Use

Be liberal with the use of subagents. This avoids polluting the main context.
Many subagents can be run in parallel. Assign specific deliverables to each subagent.

## Completion Status Protocol

When completing a workflow/task, report status using one of:

- **DONE** — All steps completed successfully. Evidence provided for each claim.
- **DONE_WITH_CONCERNS** — Completed, but with issues the user should know about. List each concern.
- **BLOCKED** — Cannot proceed. State what is blocking and what was tried.
- **NEEDS_CONTEXT** — Missing information required to continue. State exactly what you need.

### Escalation

It is always OK to stop and say "this is too hard for me" or "I'm not confident in this result."

Bad work is worse than no work. You will not be penalized for escalating.

- If you have attempted a task 3 times without success, STOP and escalate.
- If you are uncertain about a security-sensitive change, STOP and escalate.
- If the scope of work exceeds what you can verify, STOP and escalate.

Escalation format:

```
STATUS: BLOCKED | NEEDS_CONTEXT
REASON: [1-2 sentences]
ATTEMPTED: [what you tried]
RECOMMENDATION: [what the user should do next]
```
