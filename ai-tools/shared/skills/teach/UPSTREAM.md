# Teach upstream source

Imported unchanged from [Matt Pocock's teach skill](https://github.com/mattpocock/skills/tree/ad5400b930b297b0c0cb231ff1ffe42a7da666aa/skills/productivity/teach).

- Repository: `mattpocock/skills`
- Revision: `ad5400b930b297b0c0cb231ff1ffe42a7da666aa`
- Source directory: `skills/productivity/teach`
- License: MIT. The upstream repository's license is included as `LICENSE`.
- Local additions: `LICENSE` and this provenance note. No upstream files were changed.

The import retains `disable-model-invocation: true` in `SKILL.md` and
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. Their exact
visibility and invocation behavior depends on the harness. They do not restrict
ordinary filesystem access to the skill files.

Use a dedicated learning workspace. Lessons and learning records belong in
the directory where the skill is invoked, not in its installation directory.
