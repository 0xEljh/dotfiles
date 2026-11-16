## Design review

These roles review proposals, plans, and design documents, not implementations.
After drafting a non-trivial design, run `reviewer`: Fable 5.2 at high effort
through the Claude Agent SDK. It is the normal broad review, not an escalation
reserved for exceptional designs.

Normally add at least one different-model reviewer. Best chosen by design
requirements:

- `reviewer-independent` (Muse Spark): another broad perspective when no
  narrower specialist fits, or when another independent review would help.
- `reviewer-architecture` (GLM 5.3 Flash): independent reframing, alternatives,
  module boundaries, coupling, APIs, and hard-to-reverse decisions.
- `reviewer-systems` (Fable 5.2): state, lifecycle, distributed behavior,
  migrations, rollout, and cross-boundary failure handling.
- `reviewer-redteam` (GLM 5.3): security, concurrency, destructive operations,
  privacy, and public boundaries.

Use these specialists liberally wherever their perspective could change the
design, not only for critical work.

These are advisory defaults, not a fixed review count. A narrow, low-risk design
may need only the default. Broad or consequential designs often benefit from
several specialists. State the reason when using fewer reviews. Do not run every
role mechanically. Never silently substitute a non-SDK provider or older model
if the configured SDK model is unavailable; report the blocker.

Run discovery reviewers independently and preferably in parallel. Give each the
proposal, requirements, and relevant repository context, not other reviewers'
findings. After discovery, use `reviewer-referee` to reconcile overlapping
reports or disputed findings.

Reviewers can be wrong. Accept or push back on each finding with justification.
Escalate non-trivial decisions to me using the `question` tool.

Finally, before publishing a design doc (this isn't always necessary for
research or other notes), call the `readability` agent on it.

## Implementation Review

Before static implementation review, run independent consumer-level acceptance:

- `acceptance-contract`: black-box checks of the supplied acceptance criteria.
- `acceptance-redteam`: source-informed attempts to break the intended
  guarantees.
- `acceptance-user`: black-box task completion without prescribed steps. Add it
  for multi-step workflows, onboarding, and integrations, or when requested.

Use the contract verifier and adversary by default for the behavior changes
listed below. Give black-box agents requirements, public docs, and a runnable
target, not source, diffs, implementer explanations, or other agents' reports.
Give the task-based user a goal rather than an acceptance script. Keep reports
independent until completion, include them in the static-review packet, and
rerun acceptance against the final target after fixes. Prefer isolated or
disposable environments and complete teardown and cleanup.

After implementation and verification, run
`implementation-reviewer`/`implementation-reviewer-heavy`.

Use heavy and multiple reviewers liberally for:

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

Implementation review is not a substitute for verification.

After the changes have been made, if creating a PR, delegate to the `pr-creator`
agent.

## OpenCode tool names

Use the available tool catalog for argument names. The shell tool is `shell`,
child-session delegation is `subagent`, and patch editing is `patch`. File tools
use `path`. Load skills by `id`. Resume child sessions with `sessionID`; select
their agent with `agent`. There is no built-in `todowrite`. Use the
`design-docs` skill when preparing a design document.

Language-server diagnostics are unavailable in this runtime. Run the project's
compiler, type checker, or linter when those checks are needed.
