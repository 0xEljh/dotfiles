---
description: Reviews proposals, plans, and designs. This kimi-k2.7 driven agent is intended for independent, lightweight reviews: simpler, go-wide consistency and correctness checks.
mode: subagent
model: opencode-go/kimi-k2.7-code
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
