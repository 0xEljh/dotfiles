---
description:
  Reviews and improves Markdown/design doc readability. Use before pushing a
  design doc to Notion.
mode: subagent
model: openai/gpt-6.1-sol#high
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

Use the technical-writing skill to improve the readability of the doc.

Further:

- If an explanation could benefit from a mermaid diagram, create it.
- Reduce jargon, or at least explain the use of it where applicable.
- Avoid the use of acronyms and/or shortforms like "A03" in the write-ups.
  These, when done in lists or tables, can be used as references, but should be
  in brackets rather than being the only reference. (e.g. instead of "B02 will
  require 12 minutes to run" write "Collecting runtime telemetry (B02) will
  require 12 minutes.")
- If there are references to code/files, if these are on github, include a link
  to those lines/files on the appropriate branch.
