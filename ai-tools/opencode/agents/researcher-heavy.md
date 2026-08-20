---
name: researcher-heavy
description:
  Deep primary-source research for difficult or consequential technical
  questions. Produces one cited Markdown artifact under docs/research/.
mode: subagent
model: openai/gpt-5.6-sol
variant: medium
permission:
  "*": deny
  read: allow
  glob: allow
  grep: allow
  list: allow
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

Load and follow the `research` skill. Use it as the complete research workflow
and write exactly one cited artifact for the delegated question.
