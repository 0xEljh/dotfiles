# Brief a worker

Give the worker the context needed to deliver its pull request. Include source
excerpts if they are unavailable from its checkout.

```markdown
# [Outcome]

[What to change, relevant context and accepted decisions, and acceptance criteria.]

## Scope

[Areas to change, exclusions, and coordination needed with other workers.]

## Delivery

- Pull request base: [branch].
- Verification: [task-specific checks beyond the repository's normal workflow].

Use your assigned worktree and the repository's normal workflow, including its
helper agents. Own implementation, verification, review responses, and pull request
delivery. Coordinate scope changes with the coordinator.

Use full words in branch names and summaries. Avoid acronyms, shorthand, and
invented tracking identifiers.

Return the pull request link, a short summary, checks and their results, and any
blockers or unfinished work. Link the pull request in your thread.
```

For dependent work, include the predecessor's pushed commit and branch. Explain
which changes the worker needs in its checkout before coding. Add setup or
shared-resource details when relevant.
