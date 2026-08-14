## Design Decisions (IMPORTANT)

- You may propose and endorse design decisions, solutions, and design patterns
  as you deem fit, with adequate justification
- **MANDATORY**: When justifying a decision, state alternatives and their pros,
  cons, and trade-offs so that a "real choice" was made.
- Avoid over-engineering; keep solutions focused (which often to have a balanced
  approach to managing coupling, abstractions and dependencies).
- Avoid speculative runtime type checks, especially in Python, and other
  defensive guards inside trusted code. Validate untrusted input at system
  boundaries; otherwise rely on established contracts.
- Unless stated otherwise, backwards compatibility is NEVER a consideration.
  Assume that we are not working with a production environment and can always
  wipe all data and start afresh.

If this was asked during a planning phase or discussion with the user, you
should provide at least 2-3 design/implementation approaches wherever
applicable. This is NOT optional.

For each approach:

```

APPROACH A: [Name]
  Summary: [1-2 sentences]
  Complexity: [Low/Med/High]
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

- At least 2 approaches required. 3+ preferred for non-trivial designs.
- One must be the **"minimal viable"** (fewest files, smallest diff, ships
  fastest).
- One must be the **"ideal architecture"** (best long-term trajectory, most
  elegant).
- One can be **creative/lateral** (unexpected approach, different framing of the
  problem).
- Note that "implementation effort" is not at all a consideration. We are
  instead concerned with complexity introduced into the system and how it would
  affect extensibility/maintainability.

## Design Documents

- Cross reference online documentation and codebases while planning or writing
  design documents.
- Include links to the relevant documentation and code snippets in these
  plans/docs. Plans may sometimes be handed off to an engineering team for
  review and implementation.
- Design documents ought to contain enough context to be read and reviewed as a
  standalone file.
- When a plan should be persisted, write it as a `.md` file in `docs/design/` so
  it is easy to review, edit, and reuse across sessions.

## Documentation and comments

Design documents are an internal function and hence intentionally not tracked on
git (but rather, Notion).

Unless stated otherwise, all surrounding documentation and comments should not
make references to design documents.

Additionally, all documentation and comments should strive to be self-contained:

- Readers should not have to read external sources it points to to understand
  the contents. Sources are for reference and further context.
- There should not be references to other local directories and repos. If it
  can't be found publicly, it probably shouldn't be a reference. Strive instead
  to be sufficiently descriptive.

## Testing Discretion

Tests capture maintained behavioral intent; they are not a coverage target. Add
one only when it protects meaningful observable behavior from a realistic
regression.

- If new or corrected behavior warrants a test, use Red-Green-Refactor: write
  the smallest meaningful test first; confirm it fails for the expected reason,
  confirm it rejects at least one credible wrong behavior, then make it pass.
- For changes with no behavior to specify, such as renames, moves, refactors,
  and deletions, add no test; run relevant existing tests or verify directly.
- Do not test internal implementation shape or assert that an old name or
  deleted symbol remains absent.
- When environmental effects make important behavior difficult to test, prefer
  keeping that behavior in testable code and placing the effects behind a small
  adapter. This is a boundary heuristic, not a mandate to add layers.
- For disposable one-off scripts, demonstrations, and evaluations, direct
  execution and observed output are normally sufficient.
- Back concurrency claims with jitter/stress tests and performance claims with
  benchmarks.

## Verification Evidence

Completion evidence must state the verification decision and its exact outcome.
Keep it concise and quote decisive output instead of full logs. Omit fields that
do not apply, but never omit the decision or the evidence supporting it.

```text
Verification:
- Decision: new red-green test | existing tests | direct observation |
  no test warranted
- Red evidence: failing command and expected failure, when applicable
- Green evidence: exact command and result
- Additional checks: lint, type check, build, benchmark, or stress test
- Limitations: relevant checks not run and why
```

## Maximise exploration/search during planning

MAXIMISE SEARCH EFFORTS. Launch multiple background agents in parallel. Look up
codebase patterns, file structures, ripgrep (rg), fff Check remote repos,
official docs, GitHub examples. Search up best practices, design considerations,
and reference implementations. NEVER stop at the first result - be exhaustive.
Yet, you should ensure that the agent performing the search has well defined
bounds and doesn't time out. Your search deliverable should have been
sufficiently well defined that the agent can complete it in time. Drilling down
based on search results is your job as an orchestrator.

## Agent Use

Be liberal with the use of subagents. This avoids polluting the main context.
Many subagents can be run in parallel. Assign specific deliverables to each
subagent so that they can respond concisely. For agents assigned search tasks,
set an appropriate timeout for them (~10 minutes). Tasks should have been
sufficiently well defined to avoid timeouts.

## Completion Status Protocol

When completing a workflow/task, report status using one of:

- **DONE** — All steps completed successfully. Evidence provided for each claim.
- **DONE_WITH_CONCERNS** — Completed, but with issues the user should know
  about. List each concern.
- **BLOCKED** — Cannot proceed. State what is blocking and what was tried.
- **NEEDS_CONTEXT** — Missing information required to continue. State exactly
  what you need.

When providing a summary, don't just wave around concepts. Provide snippets of
evidence/quotes. This can be verbatim lines of code, docs, equations etc. Don't
overuse jargon in the summary. The summary should be something that an undergrad
with little prior context can comprehend.

If anything was written/implemented, point the user towards the core substance
of it. Then give a short breakdown of what changes should be read vs skimmed.

### Escalation

It is always OK to stop and say "this is too hard for me" or "I'm not confident
in this result."

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
