---
name: design-docs
description:
  Use whenever a non-trival plan, design, or update proposal is being drawn up.
  Important when jumping to code will lock in the wrong shape or fail to manage
  complexity appropriately. Creates a structured design document and persists it
  as markdown in docs/design/.
---

# Design Document Skill

Use this skill when the user asks for planning, architecture decisions, or
technical design work that should be saved as a document.

You should also refer to the `technical-writing` skill when writing up this
document.

We are here to design before implementation. Sketch types, function signatures,
class shapes, module boundaries, etc. in pseudo-code when its critical.
Synthesize multiple requirements and constraints and consider if the proposed
design and implementation will meet it. If fleshing out more details proves a
direction wrong, throw it out and redesign.

## Workflow

1. Understand scope from arguments and conversation context.
2. Build a real mental model of the system and references:
   - Search relevant code paths and existing patterns.
   - Check external docs and reference implementations where applicable.
   - Launch subagents in parallel for broader coverage.
3. Define and compare potential approaches:
   - Provide at least 2 approaches.
   - Include one minimal viable approach.
   - Include one ideal architecture approach.
   - Include a creative/lateral approach when useful.
   - If possible, dogfood the approach or attempt to flesh out the proposed
     shape in detail via an independent agent.
4. Recommend decisions with explicit trade-offs.
5. Write the design document to disk.
6. Push the document to Notion for review via
   `notion-cat --suppress-output path/to/doc`
7. Return a concise summary with the file path, recommended next steps, and flag
   any unresolved questions.

At each step, especially steps 1 and 4, ask clarifying questions where needed.
If relaxing a constraint would lead to a significantly better design, escalate
that to the user.

## Good design

Software engineering is about managing complexity. We always need to decide if
something is worth the complexity we're paying for. This is usually a factor of:

- extensibility requirements and time horizon (one-off script vs core
  architecture component)
- hard vs soft constraints
- intended design outcomes and robustness requirements

Always try to substantiate why a design choice is worth it's complexity and how
complexity is being optimized (e.g. this shape means less complexity in the long
run because we are committed to adding X and Y)

## Output Conventions

- Write to `docs/design/` in the project root.
- Create the directory if it does not exist.
- Filename format: `<slug>-<optional-identifiers>.md`. Dates are not necessary.
- Use a short kebab-case slug based on the topic.
- Design doc versioning is not necessary. This is already handled by having them
  pushed to Notion.
- The design doc ought to include enough context (including motivations/intent)
  to be a standalone document.

## Required Document Structure

```markdown
# <Title>

## Problem Statement

## Context

## Goals

## Non-Goals

## Approaches Considered

### APPROACH A: <Name> (Minimal Viable)

Summary: <1-2 sentences> Complexity: <S/M/L/XL> Risk: <Low/Med/High> Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

### APPROACH B: <Name> (Ideal Architecture)

Summary: <1-2 sentences> Complexity: <S/M/L/XL> Risk: <Low/Med/High> Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

### APPROACH C: <Name> (Optional, Creative/Lateral)

Summary: <1-2 sentences> Complexity: <S/M/L/XL> Risk: <Low/Med/High> Pros:

- <bullet>
- <bullet>
  Cons:
- <bullet>
- <bullet>
  Reuses: <existing code/patterns leveraged>

## Decision

## Implementation Plan

## Observability

## Verification Strategy

<State the warranted verification and why: relevant existing tests, a new
red-green test, direct execution/observed output, type/lint/build checks, or no
new test.>

## Open Questions

## References
```

## Quality Bar

- Reference concrete paths where possible (for example, `src/module.ts:42`).
- Include links to external docs when they influence decisions.
- Observability of the proposed architecture/features should be considered from
  the onset.
- Keep unknowns explicit in Open Questions; do not invent certainty.
- Keep the document reviewable by someone who did not join the live planning
  session.
- Avoid references to parties such as the user or agents. No authorship either.
- UML/mermaid diagrams are always welcome if they would improve clarity

## Asking Questions + Making recommendations

Where appropriate, seek the user's input on design decisions. Ask questions.
Every recommendation made must be accompanied with justification. Trade-offs
should be explicitly spelled out. When asking questions, assume minimal context.
Restate useful pieces of context. Strive for clarity. Help the user to
understand the concepts and context via visualizations such as pseudo-code or
architecture flows where appropriate.

If this proceeds smoothly, the `Open Questions` section should not be needed.
That section is for when we have decisions we could not resolve during planning
and explicitly want to defer.
