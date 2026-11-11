# Brief a worker

Give the worker the context needed to deliver its outcome. Include source
excerpts if they are unavailable from its checkout.

```markdown
# [Outcome]

[Outcome, relevant context and accepted decisions, and acceptance criteria.]

## Scope

[Areas to work on, exclusions, and coordination needed with other workers.]

## Delivery

- Deliverable: [result and accessible output location].
- Inputs: [prerequisite findings, files, or code branches, and how to use them].
- Workspace: [assigned isolated worktree and intended starting point].
- Permissions and restrictions: [task-specific delivery instructions].
- Verification: [task-specific checks beyond the repository's normal workflow].

Use your assigned worktree and the normal workflow applicable to this task,
including helper agents and warranted reviews. Own the outcome, verification, and
review responses. Normal helpers are authorized; do not recursively launch delivery
workstreams. Coordinate scope changes with the coordinator.

Use full words in branch names and summaries. Avoid acronyms, shorthand, and
invented tracking identifiers.

Return the deliverable or its accessible location, a short summary, checks and
their results, and blockers or unfinished work. For code handoffs, commit the
intended changes and return the actual branch with no deliverable work left
uncommitted. If commits are prohibited, report that another handoff is needed.
For files, explain where to find them. Report any outstanding helper work.
```

For dependent work, explain how to obtain and use the prerequisite results before
starting. Include the findings, accessible files, or branches needed, along with
repeatable setup steps and shared-resource restrictions. Have the worker report
missing or unexpected inputs rather than silently substitute another result.

For integration, name the contributing branches, permitted conflict-resolution
scope, producer checks, and additional checks needed for the combined outcome.
