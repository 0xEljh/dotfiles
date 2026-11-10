import { readFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { createBackend } from './engines.mjs';
import pkg from './package.json' with { type: 'json' };

const help = `Usage: mermaid-check [OPTIONS] [FILE ...]

Check raw Mermaid files or Mermaid code fences in .md/.markdown files.
With no FILE, or with FILE -, read stdin. No dependency downloads at runtime.

  --mode render|syntax|pele  Default: render (official Mermaid SVG renderer)
                            syntax: Mermaid syntax only, no layout or drawing
                            pele: Pele rendering, NOT Mermaid compatibility
  --markdown                Treat every input, including stdin, as Markdown
  --config FILE             Mermaid configuration JSON (render/syntax only)
  --puppeteer-config FILE   Puppeteer launch JSON (render only)
  --pele-options FILE       Pele RenderOptions JSON, including limits (pele only)
  --help                    Show this help
  --version                 Show pinned engine versions

Exit: 0 all passed; 1 diagram failure or no Mermaid blocks; 2 usage/I/O/setup error.
Locations identify the first line of the diagram body; upstream errors are relative.
Render checks may fetch diagram-referenced resources. Use trusted inputs.
Set PUPPETEER_EXECUTABLE_PATH to override the installed browser.
Pele and syntax work is synchronous: use an external supervisor for hard deadlines.
`;

async function jsonObject(path) {
  if (path === undefined) return {};
  const value = JSON.parse(await readFile(path, 'utf8'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${path}: expected a JSON object`);
  }
  return value;
}

function diagrams(source, markdown) {
  if (!markdown) return [{ text: source, line: 1 }];
  const result = [];
  function visit(node) {
    if (node.type === 'code' && node.lang === 'mermaid') {
      result.push({ text: node.value, line: node.position.start.line + 1 });
    }
    for (const child of node.children ?? []) visit(child);
  }
  visit(fromMarkdown(source));
  return result;
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      mode: { type: 'string', default: 'render' },
      markdown: { type: 'boolean' },
      config: { type: 'string' },
      'puppeteer-config': { type: 'string' },
      'pele-options': { type: 'string' },
      help: { type: 'boolean' },
      version: { type: 'boolean' },
    },
  });
  if (values.help) { console.log(help); return 0; }
  if (values.version) {
    console.log(`mermaid-check ${pkg.version}; Mermaid ${pkg.dependencies.mermaid}; Mermaid CLI ${pkg.dependencies['@mermaid-js/mermaid-cli']}; Pele ${pkg.dependencies.pele}`);
    return 0;
  }
  const mode = values.mode;
  if (!['render', 'syntax', 'pele'].includes(mode)) throw new Error(`Unknown mode: ${mode}`);
  if (mode === 'pele' && values.config !== undefined) throw new Error('--config is for Mermaid; use --pele-options for Pele');
  if (mode !== 'pele' && values['pele-options'] !== undefined) throw new Error('--pele-options requires --mode pele');
  if (mode !== 'render' && values['puppeteer-config'] !== undefined) throw new Error('--puppeteer-config requires --mode render');
  if (positionals.filter(path => path === '-').length > 1) throw new Error('stdin (-) can only be read once');
  const paths = positionals.length ? positionals : ['-'];
  if (paths.includes('-') && process.stdin.isTTY) throw new Error('Provide a file or pipe a diagram on stdin; see --help');
  const config = await jsonObject(values.config);
  if (config.maxTextSize !== undefined && (!Number.isFinite(config.maxTextSize) || config.maxTextSize <= 0)) {
    throw new Error('maxTextSize must be a positive finite number');
  }
  const launch = await jsonObject(values['puppeteer-config']);
  const peleOptions = await jsonObject(values['pele-options']);
  let backend;
  let checked = 0;
  let failed = 0;
  let status = 0;
  try {
    for (const path of paths) {
      const name = path === '-' ? '<stdin>' : path;
      let source;
      try {
        if (path === '-') {
          process.stdin.setEncoding('utf8');
          source = '';
          for await (const chunk of process.stdin) source += chunk;
        } else {
          source = await readFile(path, 'utf8');
        }
      } catch (error) {
        console.error(`ERROR ${name}: ${error.message}`);
        status = 2;
        continue;
      }
      const blocks = diagrams(source, values.markdown || /\.(md|markdown)$/i.test(path));
      if (!blocks.length) {
        console.error(`FAIL ${name} [${mode}]: no Mermaid code blocks found`);
        status = Math.max(status, 1);
      }
      for (const block of blocks) {
        // Initialization failures are operational errors, not invalid diagrams.
        backend ??= await createBackend(mode, { config, launch, peleOptions });
        checked++;
        const location = `${name}:${block.line}`;
        try {
          await backend.check(block.text);
          console.log(`PASS ${location} [${backend.label}]`);
        } catch (error) {
          if (!backend.healthy(error)) throw new Error(`Renderer unavailable: ${error.message}`);
          failed++;
          status = Math.max(status, 1);
          console.error(`FAIL ${location} [${backend.label}]\n${error.message}`);
        }
      }
    }
  } finally {
    await backend?.close();
  }
  console.log(`Checked ${checked} diagram(s); ${failed} failed [${mode}]`);
  return status;
}

try {
  process.exitCode = await main();
} catch (error) {
  console.error(`ERROR mermaid-check: ${error.message}`);
  process.exitCode = 2;
}
