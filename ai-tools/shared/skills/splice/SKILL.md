---
name: splice
description:
  Split a design, issue list, or refactor into reviewable pull requests and
  coordinate delivery through T3 with OpenCode workers.
disable-model-invocation: true
---

# Splice

Give each independently reviewable outcome one worker, one worktree, and one pull
request. The coordinator handles scope, dependencies, and integration. Workers
own implementation and delivery.

## Start

Invoking this skill authorizes the work needed to deliver the requested pull
requests, including fetching, committing, pushing, and creating pull requests.
The harness handles permissions. Do not add separate approval steps.

If the user asks only for a proposed breakdown, present it without launching
workers. Otherwise, proceed with execution. A supplied design document has
already been reviewed and approved. Leave implementation decisions to the worker
with the relevant context.

Use full words in branch names, workstream names, worker titles, and summaries.
Avoid acronyms, shorthand, and invented tracking identifiers.

## Split by reviewable outcome

Read the source and relevant code. Group changes by whether one could be accepted
while another is rejected. Keep tightly coupled changes together when splitting
would break intermediate states or repeatedly rewrite an interface.

Account for the requested work in the breakdown, including anything deferred or
excluded and why. For each workstream, note:

- Outcome, scope, and acceptance checks.
- Dependencies, starting point, and pull request base.
- Setup or shared resources that need coordination.

Run independent work in parallel. Use stacked pull requests or wait for merges
where work depends on earlier changes. Choose concurrency to suit the work and
available resources.

## Run the workstreams

Follow [T3 mechanics](references/t3.md) and use the
[worker brief](references/worker-brief.md). Use managed tasks with
`isolation: "worktree"` and ordinary OpenCode workers, not restricted native
`general` agents.

Give workers the context and setup needed to use the repository's normal
implementation, verification, and review workflow. Keep scheduling bookkeeping
with the coordinator.

Track progress with descriptive workstream names, worker links, pull request
links, and status. Before launching dependent work, check that its prerequisites
are delivered and verified at the commit it will use.

On resume, reconcile existing workers and pull requests before launching more.
If a worker is blocked or fails, address the affected work and continue unrelated
work where possible.

## Hand off

Summarize delivered and unfinished work, verification results, and anything that
needs the user's attention. Include descriptive worker and pull request links.
