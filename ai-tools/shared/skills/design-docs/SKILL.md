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
3. Compare credible approaches:
   - Evaluate alternatives against the actual problem and constraints, including
     keeping the current design when viable. Do not invent options to meet a
     quota.
   - Use concrete examples or sketches to compare the shapes. Use a small
     prototype when it can resolve uncertainty that affects the choice.
4. Recommend decisions with explicit trade-offs.
5. Write the design document to disk.
6. Run independent design review using the current harness's review workflow.
   Address each finding with a correction or a reasoned rejection. Complete
   readability review before publication.
7. Push the document to Notion for review via
   `notion-cat --suppress-output path/to/doc`
8. Return a concise summary with the file path, recommended next steps, and flag
   any unresolved questions.

At each step, especially steps 1 and 4, ask clarifying questions where needed.
If relaxing a constraint would lead to a significantly better design, escalate
that to the user.

### Publish local screenshots/diagrams

`notion-cat` uploads local images automatically. Use paths relative to the
Markdown file, e.g. `![Settings](./screenshots/settings.png)`, each in its own
paragraph. Download remote images first. Images mixed with text or placed in
headings or table cells fail publication.

If file selection is uncertain, inspect
`notion-cat --dry-run --suppress-output path/to/doc.md`. Use `--images=alt` for
text-only publication without asset reads.

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

## Document structure

Use this as a starting structure. Merge or omit sections that add no useful
information. Keep the problem, constraints, recommendation, trade-offs, and
verification explicit.

```markdown
# <Title>

<One-sentence recommendation and the main reason for it.>

## Problem and constraints

<Problem, intended outcome, binding constraints, and relevant scope exclusions.>

## Approaches Considered

<Compare credible alternatives and their decisive trade-offs. Explain why this
recommendation wins and what it makes worse. Describe concrete costs, risks, and
reuse where they affect the choice.>

## Proposed design

<Show behavior and structure through a concrete caller or operator example. Add
types, pseudocode, or a diagram where they resolve ambiguity.>

## Verification and delivery

<State the warranted verification and why: relevant existing tests, a new
red-green test, direct execution/observed output, type/lint/build checks, or no
new test.>

<Outline dependencies or phases only when sequencing matters. State observable
completion criteria.>

## References
```

Add operational detail where relevant: state, invariants, dependencies, partial
failure, detection, and recovery. Include unresolved questions only when
present; distinguish decision blockers from questions that can wait.

## Quality Bar

- Reference concrete paths where possible (for example, `src/module.ts:42`).
- Include links to external docs when they influence decisions.
- Observability of the proposed architecture/features should be considered from
  the onset.
- Keep unresolved questions explicit; do not invent certainty.
- Keep the document reviewable by someone who did not join planning session.
- Avoid references to parties such as the user or agents. No authorship either.
- UML/mermaid diagrams strongly encouraged if they would improve clarity.
  Consider also the `statechart` skill.

## Asking Questions + Making recommendations

Where appropriate, seek the user's input on design decisions. Ask questions.
Every recommendation made must be accompanied with justification. Trade-offs
should be explicitly spelled out. When asking questions, assume minimal context.
Restate useful pieces of context. Strive for clarity. Help the user to
understand the concepts and context via visualizations such as pseudo-code or
architecture flows where appropriate.
