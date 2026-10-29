## Review Flow

After drafting a non-trivial design, run the default `reviewer` for broad
consistency and correctness. Use the other reviewers only when their trigger
applies:

- `reviewer-heavy`: material architecture, API, or hard-to-reverse decisions
- `reviewer-max`: maximum-depth broad review when an additional expensive pass
  is justified
- `reviewer-systems`: large, stateful, distributed, migration, or rollout work
- `reviewer-systems-fable`: an additional/alternative independent systems
  reviewer for critical design decisions. Can be used in conjunction with a
  max/systems review.
- `reviewer-redteam`: security, concurrency, destructive, or public boundaries
- `reviewer-referee`: reconcile two or more reports or disputed findings

Do not run the full portfolio by default, but it's okay to be liberal with this.
When multiple discovery reviewers are warranted, run them independently and
preferably in parallel; do not share one reviewer's findings with another before
`reviewer-referee` synthesis.

Reviewers can be wrong. Accept or push back on each finding with justification.
Escalate non-trivial decisions to me using the `question` tool.

Finally, before publishing a design doc (this isn't always necessary for
research or other notes), call the `readability` agent on it.

## Implementation Review

After implementation and verification, run `implementation-reviewer` for:

- Non-trivial observable behavior changes.
- Correctness-sensitive bug fixes.
- Security, concurrency, persistence, destructive-operation, or public interface
  changes.
- Material changes whose tests are new or substantially rewritten.

Skip implementation review by default for:

- Documentation-only changes.
- Renames, moves, formatting, and mechanical refactors.
- Deletions with no replacement behavior.
- Disposable scripts, demonstrations, or evaluations.

When classification is ambiguous, run the review. Frontload a complete review
packet containing the intended behavior, acceptance criteria and verification
strategy, target kind and complete diff with explicit diff semantics, changed
paths, verification evidence, known limitations, and requested focus. The
reviewer performs static inspection and does not independently rerun commands.

If review requests changes, accept or reject each finding with evidence. Make
accepted corrections, rerun verification, and rerun review after material
changes. Keep the reviewer decision, finding dispositions, and post-correction
verification evidence in the conversation.

## OpenCode tool names

Use the available tool catalog for argument names. The shell tool is `shell`,
child-session delegation is `subagent`, and patch editing is `patch`.
File tools use `path`. Load skills by `id`. Resume child sessions with
`sessionID`; select their agent with `agent`. There is no built-in `todowrite`.
Use the `design-docs` skill when preparing a design document.

Language-server diagnostics are unavailable in this runtime. Run the project's
compiler, type checker, or linter when those checks are needed.
