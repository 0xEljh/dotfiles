---
name: how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
disable-model-invocation: true
metadata:
  credits: poteto
---

# How

Explore the codebase to answer "how does X work?" questions.

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

## 1. Explain

Use the available delegation tool to ask one exploration-capable subagent to
explore and explain in one pass. Give it read-only instructions, the user's
question, and this brief:

> You are writing an architectural explanation for a senior engineer. Explore the codebase and write an explanation that a senior engineer unfamiliar with this area could read and walk away with a solid mental model. They should understand the architecture well enough to start working in it confidently. You have read-only access to the codebase.

Ask it to cite the entry points and decisive symbols, explain ownership and
important state, and give a short reading order. Separate source inspection from
any cited execution evidence and state what remains unknown.

Default to source reading. If the user also asks to run a scenario or change the
code, handle that through the normal task workflow within the original request's
authority. Label executed observations with the target actually run.

## 2. Present

Present the subagent's output. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.
