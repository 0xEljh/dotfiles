---
name: splice
description:
  Break a task list or large task into verifiable workstreams and coordinate
  full-workflow OpenCode workers through T3, without requiring pull requests.
disable-model-invocation: true
---

# Splice

Give each independently verifiable outcome a full-workflow worker and an explicit
deliverable. The coordinator handles decomposition, dependencies, handoffs, and
final integration. Workers own their normal end-to-end workflow and may use helpers.

## Start

Invoking this skill authorizes the task-local work needed for the requested
outcome, including local commits, local artifact copies, and fetching prerequisite
work from configured repository remotes. Pushing, creating pull requests, publishing,
deploying, merging into the current checkout, and deleting work are not implied.
Follow explicit task restrictions and harness permissions. Do not add separate
approval steps for ordinary task execution.

If the user asks only for a proposed breakdown, present it without launching
workers. Otherwise, proceed with execution. Preserve supplied approvals without
treating raw task notes as approved designs. Leave task-specific decisions to the
worker with the relevant context.

Use full words in branch names, workstream names, worker titles, and summaries.
Avoid acronyms, shorthand, and invented tracking identifiers.

## Split by verifiable outcome

Read the task list or large task and relevant sources. Group work by independently
verifiable outcomes. Keep tightly coupled work together when splitting would break
intermediate states or repeatedly rewrite an interface. Do not force parallelism
when one coherent workstream is better.

Account for the requested work in the breakdown, including anything deferred or
excluded and why. For each workstream, note:

- Outcome, scope, and acceptance checks.
- Dependencies and inputs needed before execution.
- Deliverable, accessible output location, and applicable workflow.
- Setup or shared resources that need coordination.
- Final integration or synthesis, when the overall outcome needs it.

Run independent work in parallel. Choose concurrency to suit the work and available
resources. When multiple code workers contribute to one combined outcome, include
an integration workstream that combines their changes in its own worktree and
verifies the result. One code worker needs no separate integrator. Leave independent
outputs separate when requested, and add synthesis only when the outcome needs it.

## Run the workstreams

Follow [T3 mechanics](references/t3.md) and use the
[worker brief](references/worker-brief.md). Use managed tasks with
`isolation: "worktree"` and ordinary OpenCode workers, not restricted native
`general` agents.

Give workers the context and setup needed to use the normal workflow applicable
to their task, including helpers and warranted reviews. Do not recursively create
delivery workstreams unless requested. Keep scheduling bookkeeping with the
coordinator. Account for shared services separately; worktrees do not isolate them.

Keep a coordinator-owned run record using the T3 procedure. Track descriptive
workstream names, worker links, outputs, and status. Before launching dependent
work, check acceptance evidence, outstanding helper work, and access to its inputs.

On resume, reconcile existing workers and outputs before launching more.
If a worker is blocked or fails, address the affected work and continue unrelated
work where possible.

## Hand off

Summarize delivered and unfinished work, verification results, and anything that
needs the user's attention. Link workers and deliverables. Distinguish independent
outputs from the verified final result and state where that result lives.
