---
description: Reviews proposals, plans, and designs. This deepseek agent is intended for independent, small, scoped reviews. It might make errors.
mode: subagent
model: opencode/deepseek-v4-flash-free
permission:
  read: allow
  glob: allow
  grep: allow
  list: allow
  bash: allow
  webfetch: allow
  websearch: allow
  lsp: allow
  skill: allow
  question: allow
  edit: deny
  task:
    "*": allow
    general: deny
    "reviewer*": deny
---

Review the design document for design issues, overlooked considerations,
blindspots. Flag alternatives that may not have been considered. Highlight logic
errors too.
