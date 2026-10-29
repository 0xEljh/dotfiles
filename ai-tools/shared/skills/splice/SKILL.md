---
name: splice
description:
  Split a design, issue list, or refactor into reviewable PRs. Execution uses T3
  with verified OpenCode workers.
disable-model-invocation: true
---

# Splice

Give each independently reviewable outcome one worker, one worktree, and one PR.
Coordinate scope and dependencies. Let workers implement. Do not target a worker
count.

## Start

- **Propose:** read the source and relevant code. Account for every source item
  and present a breakdown. Do not launch workers.
- **Execute:** confirm execution approval, check capabilities, and note the
  workstreams before launch.
- **Resume:** read the breakdown and worker links. Reconcile tasks and PRs before
  scheduling.

For execution or resume, follow [T3 mechanics](references/t3.md).

When this skill is executed on a design doc, that doc has already been reviewed
and approved. So do proceed on that basis. This doesn't meant you can't escalate
non-trivial decisions regarding irreversible decisions. Ambiguity in the macro
design is often intentional: to leave micro-decisions to the agent working
closer to the problem which hence has more context.

## Split by reviewable outcome

Consider if one change could be rejected while its neighbor is accepted. Keep
tightly coupled changes together if splitting breaks intermediate states or
repeatedly rewrites an interface. For wide migrations, consider an enabling PR,
parallel caller changes, then removal (but don't shy away from removal). Prefer
one atomic PR when coexistence costs more than it helps. This doesn't mean to
avoid merge conflicts: that is expected behavior, but is often better when
expected/intended.

Give every source item a stable label. Assign it to a workstream, link it to the
item it duplicates, or defer or exclude it with a reason. Do not create tracker
issues.

For each workstream, record:

- Outcome, acceptance criteria, source labels, likely owned paths, and
  exclusions.
- Verification and review under the repository's normal workflow.
- Dependencies, released at `pr_ready` or `merged`.
- Starting commit and PR base branch, separate from scheduling dependencies. For
  future predecessors, agree how to resolve the commit and pin its full ID
  before launch.
- Shared resources that could conflict: ports, databases, generated files.

Recommend independent, stacked, or merge-first delivery for each dependency.
Divergent unmerged predecessors must merge first. Fast-forward adoption cannot
combine them.

Get execution approval for:

- The exact coordinator baseline commit and breakdown.
- `maxParallel`, worker provider and model from T3's live catalog.
- Branch strategy, failure policy, and allowed replacement attempts.
- Separate permissions to commit, push, fetch from approved remotes, and create
  PRs.

Get approval for scope or concurrency changes. Path-overlap warnings are
advisory. Tool and external-action restrictions remain binding.

## Run the workstreams

Use managed T3 tasks with `isolation: "worktree"` and ordinary OpenCode workers,
not restricted native `general` agents. Do not raise global depth limits.

It's often also useful to setup the worktree with its necessary dependencies
(e.g. symlinked environment files)

Keep one active coordinator. Use the [worker brief](references/worker-brief.md)
for task-specific context, scope, checks, and delivery permissions. Keep scheduling
and task metadata on the coordinator side. Workers own implementation through PR
delivery, using normal exploration and review helpers. The coordinator owns
scope, dependencies, and integration decisions.

Keep at most `maxParallel` delivery workers live, including workers waiting on
questions or tools. Unlaunched blocked work gets no slot or worktree. Helpers
follow the normal repository workflow. Worktrees do not isolate credentials,
filesystem access, caches, processes, ports, or databases (but that's also not
the intention: set these up where appropriate to facilitate the worker)

Release dependencies from fresh task, PR-host, and commit-specific evidence, not
a successful turn or PR URL. After failure, continue unrelated work only if the
approved policy allows.

## Pause and hand off

Pause affected work on capability mismatch, baseline drift, unsafe setup,
divergent adoption, stale or missing evidence, or uncertain launch or ownership.
Ask for a decision, not a reset, rebase, force-push, unauthorized replacement,
or scope expansion.

Human takeover pauses scheduling and automatic cancellation for affected work.
Reconcile explicitly before resuming. Stop owned tasks with cancellation, not
worktree deletion. Keep worker links and notes for resume.

Merges, force-pushes on unrelated branches, tracker creation, and cleanup need
separate authorization. Default to explicit resume after merges. Monitoring is
opt-in through T3's watch tools, not polling.

Report delivered, blocked, failed, cancelled, and deferred work separately.
Include source coverage, worker and PR links, verification gaps, and the next
decision. Reconcile the coordinator's PR links before handoff.
