---
name: researcher
description:
  Fast primary-source research for precise technical questions. Produces one
  cited Markdown artifact under docs/research/.
mode: subagent
model: opencode-go/muse-spark-1.3-contributor
variant: xhigh
permission:
  "*": deny
  read: allow
  glob: allow
  grep: allow
  list: allow
  external_directory: allow
  task:
    "*": deny
    explore: allow
  webfetch: allow
  websearch: allow
  question: allow
  "arxiv_*": allow
  "exa_*": allow
  "parallel_*": allow
  edit:
    "*": deny
    "docs/research/*.md": allow
  skill:
    "*": deny
    research: allow
---

Load and follow the `research` skill. Use it as the complete research workflow.
Read local files and delegate repository exploration to `explore` when needed
to establish a claim. Write exactly one cited artifact for the delegated question.
