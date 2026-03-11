## Design Decisions

- You may propose and endorse design decisions, solutions, and design patterns as you deem fit, with adequate justification
- When justifying a decision, state alternatives and their pros, cons, and trade-offs so that a "real choice" was made
- Avoid over-engineering; keep solutions simple

## Design Documents

- Cross reference online documentation and codebases while planning or writing design documents.
- Include links to the relevant documentation and code snippets in these plans/docs. Plans may sometimes be handed off to an engineering team for review and implementation.

## Test Driven Development (TDD)

If the codebase has tests, then TDD should be adopted. That is, tests should be written first, then code. Only as a final step do we wrangle tests and code. This reduces "empty tests" and also limits spec drift.

## Maximise exploration/search during planning

MAXIMISE SEARCH EFFORTS. Launch multiple background agents in parallel.
Look up codebase patterns, file structures, ripgrep (rg)
Check remote repos, official docs, GitHub examples.
Search up best practices, design considerations, and reference implementations.
NEVER stop at the first result - be exhaustive.

## Agent Use

Be liberal with the use of subagents. This avoids polluting the main context.
Many subagents can be run in parallel. Assign specific deliverables to each subagent.
