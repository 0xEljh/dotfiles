---
name: design
description: Create a structured design document and persist it as markdown in docs/design/.
---

# Design Document Skill

Use this skill when the user asks for planning, architecture decisions, or technical design work that should be saved as a document.

## Workflow

1. Understand scope from arguments and conversation context.
2. Research thoroughly:
   - Search relevant code paths and existing patterns.
   - Check external docs and reference implementations when needed.
   - Launch subagents in parallel for broader coverage.
3. Define and compare approaches:
   - Provide at least 2 approaches.
   - Include one minimal viable approach.
   - Include one ideal architecture approach.
   - Include a creative/lateral approach when useful.
4. Recommend decisions with explicit trade-offs.
5. Write the design document to disk.
6. Return a concise summary with the file path and unresolved questions.

At each step, especially steps 1 and 4, ask clarifying questions where needed.

## Output Conventions

- Write to `docs/designs/` in the project root.
- Create the directory if it does not exist.
- Filename format: `<slug>-<optional-identifiers>.md`.
- Use a short kebab-case slug based on the topic.

## Required Document Structure

```markdown
# <Title>

## Problem Statement

## Context

## Goals

## Non-Goals

## Approaches Considered

### APPROACH A: <Name> (Minimal Viable)

Summary: <1-2 sentences>
Effort: <S/M/L/XL>
Risk: <Low/Med/High>
Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

### APPROACH B: <Name> (Ideal Architecture)

Summary: <1-2 sentences>
Effort: <S/M/L/XL>
Risk: <Low/Med/High>
Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

### APPROACH C: <Name> (Optional, Creative/Lateral)

Summary: <1-2 sentences>
Effort: <S/M/L/XL>
Risk: <Low/Med/High>
Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

## Decision

## Implementation Plan

## Observability

## Test Strategy

## Open Questions

## References
```

## Quality Bar

- Reference concrete paths where possible (for example, `src/module.ts:42`).
- Include links to external docs when they influence decisions.
- Observability of the proposed architecture/features should be considered from the onset.
- Keep unknowns explicit in Open Questions; do not invent certainty.
- Keep the document reviewable by someone who did not join the live planning session.

## Asking Questions + Making recommendations

Where appropriate, seek the user's input on design decisions. Ask questions.
Every recommendation made must be accompanied with justification. Trade-offs should be explicitly spelled out.
When asking questions, assume minimal context. Restate useful pieces of context.
Strive for clarity.
Help the user to understand the concepts and context via visualizations such as pseudo-code or architecture flows where appropriate.

If this proceeds smoothly, the `Open Questions` section should not be needed.
That section is for when we have decisions we could not resolve during planning.
