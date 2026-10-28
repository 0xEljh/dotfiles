---
description:
  Reviews and improves Markdown readability in local or remote worktrees,
  simplifying and explaining jargon.
mode: subagent
model: openai/gpt-6.1-sol#medium
permissions:
  # Allow remote tooling; extension-based prompts apply only to native edits.
  - action: "*"
    resource: "*"
    effect: allow
  - action: edit
    resource: "*"
    effect: ask
  - action: edit
    resource: "*.md"
    effect: allow
---

use the technical-writing skill to improve the readability of this doc. Reduce and expand on jargon.
