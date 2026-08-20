## Making decisions

- You may propose and endorse design decisions, solutions, and design patterns
  as you deem fit, with adequate justification
- When justifying a decision, state alternatives that were considered, their
  pros & cons & trade-offs so that a "real choice" was made.
- Slant for decisions: avoid over-engineering; keep solutions focused (which
  often means balanced approach to managing coupling, abstractions and
  dependencies). (Long term) complexity is the enemy.
- Unless stated otherwise, backwards compatibility is NEVER a consideration.
  Assume that we are not working with a production environment and can always
  wipe all data and start afresh. No decision should be based on backwards
  compatibility.
- The majority of what's written here, spoken by the user, or written up in
  skill files are guidelines, not rules. Depart from it when doing would improve
  the outcome of what you're doing (but report on this both during and after).

Break any of these rules sooner than say anything outright barbarous. —George
Orwell, "Politics and the English Language"

## Design Documents

- Design documents are an internal function that live in the docs/design/ folder
  and are intentionally kept out of git (but not gitignored either). Instead, we
  track and version via Notion with the custom cli command `notion-cat`.
- Any feature that deserves review or that might need building in phases should
  live in a design document.
- Use the `design` skill when planning and writing up these documents.
- As an internal only document, all other documentation and code comments should
  not make references to design documents

## Documentation and comments

All documentation and comments should strive to be self-contained:

- Readers should not have to read external reference to to understand the
  content of what is written. Sources are for reference and further context.
- In most cases, there should not be references made to other local directories
  and repos. If it can't be found publicly, it probably shouldn't be a
  reference. Strive instead to be sufficiently descriptive.

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

### Escalation

It is always okay to stop and say "this is too hard", "I don't have enough
context"

Bad work is worse than no work. You will not be penalized for escalating.

- If you have attempted a task 3 times without success, STOP and escalate.
- If you are uncertain about a security-sensitive change, STOP and escalate.
- If the scope of work exceeds what you can verify, STOP and escalate.

## Completion Status Protocol

When completing a workflow/task, report status using one of:

- **DONE** — All steps completed successfully. Evidence provided for each claim.
- **DONE_WITH_CONCERNS** — Completed, but with issues the user should know
  about. List each concern.
- **BLOCKED** — Cannot proceed. State what is blocking and what was tried.
- **NEEDS_CONTEXT** — Missing information required to continue. State exactly
  what you need.

### Concluding Summary

When providing a summary, don't just wave around concepts. Provide snippets of
evidence/quotes. This can be verbatim lines of code, docs, equations etc. Don't
overuse jargon in the summary. The summary should be something that an undergrad
with little prior context can comprehend.

If anything was written/implemented, point the user towards the core substance
of it. Then give a short breakdown of what changes should be read vs skimmed.

### Verification Evidence

Completion evidence must state the verification decision and its exact outcome.
Keep it concise and quote decisive output instead of full logs. Omit fields that
do not apply, but never omit the decision or the evidence supporting it.

```
Verification:
- Decision: new red-green test | existing tests | direct observation |
  no test warranted
- Red evidence: failing command and expected failure, when applicable
- Green evidence: exact command and result
- Additional checks: lint, type check, build, benchmark, or stress test
- Limitations: relevant checks not run and why
```
