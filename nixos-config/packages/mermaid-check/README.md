# Verify Mermaid diagrams

`mermaid-check` validates raw Mermaid text and Mermaid code fences in Markdown.
Dependencies are pinned and installed once through the shared Nix packages.
The command does not run npm or download a browser at runtime.

```sh
mermaid-check diagram.mmd
mermaid-check proposal.md other-diagram.mmd
printf 'flowchart TD\nA --> B\n' | mermaid-check
cat proposal.md | mermaid-check --markdown
```

## Choose a check

The default, `--mode render`, uses the official Mermaid CLI renderer to create
and inspect an SVG, then discards it. One browser serves the whole invocation.
It checks parsing, layout, and drawing, but not visual quality. Font embedding
is disabled because the generated SVG is not distributed.

```sh
mermaid-check --mode syntax diagram.mmd
mermaid-check --mode pele chart.mmd
```

`syntax` uses Mermaid's parser with a DOM shim. It does not check rendering.
`pele` uses Pele's own synchronous renderer. It is much faster, but its grammar
and layouts differ from Mermaid. A Pele pass certifies only Pele rendering.
Pele 1.9.0 targets Mermaid 12.1 compatibility; this tool pins Mermaid 12.2.0.
Use `--version` to see the installed versions.

## Inputs and results

Files ending in `.md` or `.markdown` are Markdown. Other files and stdin are raw
Mermaid unless you pass `--markdown`. Quote and list fences are supported. Fences
inside a larger example code block are not diagrams. Frontmatter and Mermaid init
directives inside a diagram are preserved.

The command checks all blocks and reports their first body line. Locations inside
an upstream error message are relative to that diagram, not the whole document.
No Mermaid blocks is a failure, not a successful zero-diagram check.

- Exit `0`: every selected check passed.
- Exit `1`: a diagram failed, or a Markdown input contained no Mermaid blocks.
- Exit `2`: an option, file, configuration, or renderer setup failed. Other diagram
  errors remain visible if an I/O error also occurs.

Use `--` before a filename that starts with a dash. Use `-` to read stdin among
file arguments. Stdin can be read only once.

## Browser and configuration

Linux uses Nix-provided Chromium. On macOS, install Google Chrome in
`/Applications`, or set `PUPPETEER_EXECUTABLE_PATH` to a compatible browser.
Browser startup failure is exit `2`. There is no automatic download or fallback
to syntax-only verification. Run the normal Nix configuration rebuild to put the
command on PATH and distribute the shared instructions to each agent.
Before rebuilding, you can run it from `nixos-config` with
`nix run .#mermaid-check -- diagram.mmd` once the new files are tracked by Git.

```sh
mermaid-check --config mermaid.json diagram.mmd
mermaid-check --puppeteer-config browser.json diagram.mmd
mermaid-check --mode pele --pele-options pele.json chart.mmd
```

`--config` passes a Mermaid configuration object to render or syntax checks.
Increase `maxTextSize` there for larger diagrams. Render checks reject oversized
source instead of accepting Mermaid's “maximum text size” replacement diagram.

`--puppeteer-config` passes a Puppeteer launch object. Its explicit options take
precedence over the default browser and environment. The tool does not disable
Chromium's sandbox. In an environment that needs `--no-sandbox`, supply that
argument explicitly and accept the security trade-off. Launch and protocol
timeouts can also be set there.

`--pele-options` passes Pele RenderOptions, including `config`, `limit`,
`outputLimit`, and `maxEdges`. Without it, Pele's defaults apply. For large
statecharts, the existing workflow uses:

```json
{"limit":1000000,"outputLimit":8000000,"maxEdges":10000}
```

These files are explicit trusted overrides; no project configuration is discovered
automatically. Rendering runs locally but may fetch diagram-referenced remote
resources and disclose their URLs. This is not an offline or untrusted-code
sandbox. Synchronous parsing and Pele rendering have no tool-level wall-clock
deadline. Use a process supervisor such as `timeout` if you need a hard deadline.

## Develop and measure

From this package directory:

```sh
npm ci --ignore-scripts
MERMAID_CHECK_BROWSER=/path/to/chromium npm test
MERMAID_CHECK_BROWSER=/path/to/chromium npm run benchmark
```

Without a browser environment variable, the test runner explicitly skips browser
tests. Linux Nix builds run all tests with their packaged Chromium. macOS browser
execution must be verified separately.
