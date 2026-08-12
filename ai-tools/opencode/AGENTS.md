## Review Flow

After drafting a non-trivial design, run the default `reviewer` for broad
consistency and correctness. Use the other reviewers only when their trigger
applies:

- `reviewer-alt`: material architecture, API, or hard-to-reverse decisions
- `reviewer-systems`: large, stateful, distributed, migration, or rollout work
- `reviewer-redteam`: security, concurrency, destructive, or public boundaries
- `reviewer-referee`: reconcile two or more reports or disputed findings

Do not run the full portfolio by default. When multiple discovery reviewers are
warranted, run them independently and preferably in parallel; do not share one
reviewer's findings with another before `reviewer-referee` synthesis.

Reviewers can be wrong. Accept or push back on each finding with justification.
Escalate non-trivial decisions to me using the `question` tool.
