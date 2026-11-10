# Statechart upstream source

Imported from [pstack's statechart skill](https://github.com/cursor/plugins/tree/4dd1d05818d19792e7cd6e818f1667f95f7e1dbe/pstack/skills/statechart).

- Repository: `cursor/plugins`
- Revision: `4dd1d05818d19792e7cd6e818f1667f95f7e1dbe`
- Source directory: `pstack/skills/statechart`
- License: MIT. The upstream plugin's license is included as `LICENSE`.
- Local additions: `LICENSE` and this provenance note.
- Local changes: `SKILL.md` is line-wrapped and adds a `mermaid-check` validation
  instruction before sharing diagrams. All other upstream files are unchanged.

The import includes the launcher, TypeScript sources, rendering guide, tests,
and test fixtures. The launcher and test adapter retain their executable modes.

The skill is explicitly invoked with `/statechart`. Its
`disable-model-invocation: true` setting is retained. Invocation behavior depends
on the harness.

The launcher supports Node 22.18 through 22.x, Node 23.6 or later, Bun 1.1.5 or
later, or Deno 2.0 or later. Model checking requires TLC 1.7.4 and Java 11 or
later. By default, the first run downloads TLC and Java through `mise`.
`TLA2TOOLS` can instead name an existing `tla2tools.jar`, and `JAVA` can name a
Java executable. Pele 1.9.0 is needed only for SVG rendering.
