---
name: splice-pr
description:
  Split a design, issue list, or refactor into independently reviewable pull
  requests and coordinate full-workflow OpenCode workers through T3.
disable-model-invocation: true
---

# Splice PR

Read these shared instructions relative to this skill's directory:

- [Splice](../splice/SKILL.md).
- [T3 mechanics](../splice/references/t3.md).
- [Worker brief](../splice/references/worker-brief.md).

Resolve links within those files relative to their containing directories. Use
their coordination workflow with the PR delivery requirements below replacing
the default local-deliverable policy.

## Deliver one pull request per workstream

Give each independently reviewable outcome one worker, one worktree, and one pull
request. Split by whether one change can be accepted while another is rejected.
Do not add a final integration branch by default. Identify any combined checks
needed without automatically combining independent pull requests.

Invoking this skill authorizes fetching, committing, pushing, and creating the
requested pull requests, subject to explicit restrictions and harness permissions.
Do not add a separate approval step. A breakdown-only request launches nothing.
Carry forward supplied approvals and leave implementation decisions with the worker.
Merging and destructive cleanup are not implied.

## Extend the plan and worker brief

Record the pull request base separately from scheduling dependencies and starting
code. Set each worker's deliverable to its pull request and its delivery permissions
to fetching, committing, pushing, and PR creation, subject to task restrictions.
Add the PR base, prerequisite details, and return requirements below to the shared
brief. Do not also include contradictory no-PR instructions. Workers own
implementation, verification, review responses, and PR delivery.

For stacked delivery, give the next worker the predecessor's verified pushed
changes and actual branch as its PR base. For merge-first work, confirm the merge
with the hosting service and start from code containing the result. Do not silently
combine divergent prerequisites into one starting point.

Require the PR link, actual branch, checks and results, review dispositions, and
unfinished work. Before releasing dependent work, query the host and check that
verification and required reviews apply to the current PR changes. Pending or
failed required checks do not satisfy readiness. Reconcile predecessor updates.

## Link and hand off

Workers link pull requests in their threads. The coordinator also calls
`link_pull_request` for every pull request it coordinates, including every layer
of a stack. Before handoff, call `list_thread_pull_requests` and link any that are
missing. Report linking failures.

When monitoring is requested, use `watch_pull_request` and end the turn. Reconcile
the hosting service's state on wake. Call `unwatch_pull_request` before handoff.

Return descriptive worker and pull request links, verification results, unfinished
work, and anything requiring attention.
