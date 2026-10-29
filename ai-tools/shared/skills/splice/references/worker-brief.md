# Brief a worker

Give the worker what it needs to deliver its PR, not the coordinator's bookkeeping.
Omit irrelevant fields. Include relevant source excerpts if the worker cannot
read them from its checkout.

```markdown
# [Outcome]

[What to change, relevant context and accepted decisions, and acceptance criteria.]

## Scope

[Areas to change, exclusions, and coordination needed with other workers.]

## Delivery

- PR base: [branch].
- Verification: [task-specific checks beyond the repository's normal workflow].
- Permissions: [approved commit, push, fetch, and PR actions; relevant limits].

Use your assigned worktree and the repository's normal workflow, including its
helper agents. Own implementation, verification, review responses, and the PR.
Ask the coordinator about changes outside this scope. Do not create further
delivery workstreams without approval.

Return the PR link, a short summary, checks run and their results, and any blockers
or unfinished work. Link the PR in your thread when creating or starting work on
it. Leave monitoring and dependency release to the coordinator.
```

For dependent work only, add the pinned starting commit and instruct the worker
to adopt it with `git merge --ff-only "<commit>"` before coding, confirm `HEAD`
matches, and rerun any setup affected by the change. Have it ask if adoption fails.

Add setup or shared-resource instructions only when the task needs them. Keep
run, attempt, task, and thread IDs out of the brief. Read T3 metadata from tools
rather than asking workers to repeat it.
