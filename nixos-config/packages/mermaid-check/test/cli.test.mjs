import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

const cli = fileURLToPath(new URL('../cli.mjs', import.meta.url));
function run(args, input, cwd) {
  return spawnSync(process.execPath, [cli, ...args], {
    input, cwd, encoding: 'utf8', timeout: 90_000,
  });
}

test('syntax mode accepts valid labels and rejects malformed syntax', () => {
  const valid = run(['--mode', 'syntax'], 'flowchart TD\nA[hello] --> B[world]');
  assert.equal(valid.status, 0, valid.stderr);
  assert.match(valid.stdout, /syntax/);
  const invalid = run(['--mode', 'syntax'], 'flowchart TD\nA[unclosed');
  assert.equal(invalid.status, 1);
  assert.match(invalid.stderr, /Parse error/);
});

function fixture(t, files) {
  const dir = mkdtempSync(join(tmpdir(), 'mermaid-check-test-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text);
  return name => join(dir, name);
}

const browserAvailable = Boolean(process.env.PUPPETEER_EXECUTABLE_PATH || process.env.MERMAID_CHECK_BROWSER);
const renderOptions = { skip: browserAvailable ? false : 'Set MERMAID_CHECK_BROWSER to run browser checks' };
const ganttRenderFailure = 'gantt\ndateFormat YYYY-MM-DD\nsection Test\nTask :a, b, c, d, 2026-01-01, 1d';

test('default mode actually renders common diagram types', renderOptions, t => {
  const files = {
    'flow.mmd': 'flowchart TD\nA[hello] --> B[world]',
    'sequence.mmd': 'sequenceDiagram\nAlice->>Bob: Hello',
    'class.mmd': 'classDiagram\nclass Animal {\n+String name\n+run()\n}',
    'state.mmd': 'stateDiagram-v2\n[*] --> Idle\nIdle --> Running: start',
    'er.mmd': 'erDiagram\nA ||--o{ B : has',
    'gantt.mmd': 'gantt\ndateFormat YYYY-MM-DD\nsection Work\nBuild :a1, 2026-11-01, 3d',
    // A label containing the word "error" must not trigger error-SVG detection.
    'error-label.mmd': 'flowchart TD\nA[Syntax error in text] --> B',
  };
  const path = fixture(t, files);
  const result = run(Object.keys(files).map(path));
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Mermaid full render/);
  assert.match(result.stdout, /Checked 7 diagram\(s\); 0 failed/);
});

test('render ignores incidental Puppeteer configuration in the working directory', renderOptions, t => {
  const path = fixture(t, {
    '.puppeteerrc.cjs': 'throw new Error("incidental Puppeteer configuration executed");',
  });
  const result = run([], 'flowchart TD\nA --> B', path('.'));
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Mermaid full render/);
});

test('render catches an error that syntax alone accepts', renderOptions, () => {
  assert.equal(run(['--mode', 'syntax'], ganttRenderFailure).status, 0);
  const rendered = run([], ganttRenderFailure);
  assert.equal(rendered.status, 1);
  assert.match(rendered.stderr, /reading 'type'/);
  assert.match(rendered.stderr, /Mermaid full render/);
});

test('valid info diagrams do not require an SVG viewBox', renderOptions, () => {
  const result = run([], 'info');
  assert.equal(result.status, 0, result.stderr);
});

test('malformed raw input fails rendering', renderOptions, () => {
  const result = run([], 'flowchart TD\nA[unclosed');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /Parse error/);
});

test('Markdown extraction handles nested fences, ignores examples, and continues after failures', t => {
  const path = fixture(t, { 'diagrams.md': [
    '# Diagrams',
    '> ~~~mermaid',
    '> sequenceDiagram',
    '> Alice->>Bob: Hello',
    '> ~~~',
    '',
    '````markdown',
    '```mermaid',
    'not a real diagram',
    '```',
    '````',
    '',
    '```mermaid',
    'flowchart TD',
    'A[unclosed',
    '```',
    '',
    '- List:',
    '',
    '  ```mermaid',
    '  flowchart TD',
    '  A --> B',
    '  ```',
  ].join('\n') });
  const result = run(['--mode', 'syntax', path('diagrams.md')]);
  assert.equal(result.status, 1);
  assert.match(result.stdout, /diagrams\.md:3 /);
  assert.match(result.stderr, /diagrams\.md:14 /);
  assert.match(result.stdout, /diagrams\.md:21 /);
  assert.match(result.stdout, /Checked 3 diagram\(s\); 1 failed/);
});

test('Markdown stdin preserves diagram frontmatter and init directives', () => {
  const result = run(['--mode', 'syntax', '--markdown', '-'], [
    '```mermaid',
    '---', 'title: Hello', '---',
    '%%{init: {"theme":"dark"}}%%',
    'flowchart TD', 'A --> B', '```',
  ].join('\n'));
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /<stdin>:2 /);
});

test('empty raw diagrams and Markdown without Mermaid blocks fail', () => {
  assert.equal(run(['--mode', 'syntax'], '').status, 1);
  const result = run(['--markdown', '--mode', 'syntax'], '# No diagrams\n```js\nhi\n```');
  assert.equal(result.status, 1);
  assert.match(result.stderr, /no Mermaid code blocks/);
});

test('I/O errors take precedence without hiding diagram failures', t => {
  const path = fixture(t, { 'bad.mmd': 'flowchart TD\nA[unclosed' });
  const result = run(['--mode', 'syntax', path('missing'), path('bad.mmd')]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /ENOENT/);
  assert.match(result.stderr, /Parse error/);
});

test('bad options and invalid JSON configuration are operational errors', t => {
  const path = fixture(t, { 'invalid.json': '{', 'array.json': '[]' });
  for (const args of [
    ['--mode', 'unknown'], ['--unknown'], ['-', '-'],
    ['--mode', 'syntax', '--config', path('invalid.json')],
    ['--mode', 'syntax', '--config', path('array.json')],
    ['--mode', 'pele', '--config', path('array.json')],
    ['--mode', 'syntax', '--puppeteer-config', path('array.json')],
  ]) assert.equal(run(args, 'flowchart TD\nA --> B').status, 2, args.join(' '));
});

test('empty configuration filenames are operational errors, including incompatible modes', () => {
  for (const args of [
    ['--mode', 'syntax', '--config', ''],
    ['--mode', 'pele', '--pele-options', ''],
    ['--puppeteer-config', ''],
    ['--mode', 'pele', '--config', ''],
    ['--mode', 'syntax', '--pele-options', ''],
    ['--mode', 'syntax', '--puppeteer-config', ''],
  ]) {
    const result = run(args, 'flowchart TD\nA --> B');
    assert.equal(result.status, 2, JSON.stringify(args));
    assert.match(result.stderr, /ERROR mermaid-check/);
    assert.doesNotMatch(result.stdout, /PASS/);
  }
});

for (const mode of ['syntax', 'render']) {
  test(`invalid Mermaid configuration is an operational error in ${mode} mode`,
    mode === 'render' ? renderOptions : {}, t => {
      const path = fixture(t, {
        'bad.json': '{"theme":"base","themeVariables":{"primaryColor":"certainly not a color"}}',
        'good.json': '{"theme":"base","themeVariables":{"primaryColor":"#ff0000"}}',
      });
      const bad = run(['--mode', mode, '--config', path('bad.json')], 'flowchart TD\nA --> B');
      assert.equal(bad.status, 2, bad.stderr);
      assert.match(bad.stderr, /ERROR mermaid-check.*Unsupported color format/);
      assert.doesNotMatch(bad.stderr, /FAIL <stdin>/);
      const good = run(['--mode', mode, '--config', path('good.json')], 'flowchart TD\nA --> B');
      assert.equal(good.status, 0, good.stderr);
    });
}

test('browser protocol failures are operational errors even while the browser is connected', renderOptions, t => {
  for (const type of ['ProtocolError', 'TimeoutError']) {
    // Simulate the renderer's dependency failure without a timing-sensitive deadline.
    const renderer = `import { ${type} } from ${JSON.stringify(import.meta.resolve('puppeteer'))};
      export async function renderMermaid() { throw new ${type}('simulated browser timeout'); }`;
    const path = fixture(t, {
      'preload.mjs': `import { registerHooks } from 'node:module';
        registerHooks({ resolve(specifier, context, nextResolve) {
          if (specifier === '@mermaid-js/mermaid-cli') {
            return { url: ${JSON.stringify(`data:text/javascript,${encodeURIComponent(renderer)}`)}, shortCircuit: true };
          }
          return nextResolve(specifier, context);
        } });`,
    });
    const result = spawnSync(process.execPath, ['--import', path('preload.mjs'), cli], {
      input: 'flowchart TD\nA --> B', encoding: 'utf8', timeout: 30_000,
    });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /ERROR mermaid-check.*simulated browser timeout/);
    assert.doesNotMatch(result.stderr, /FAIL <stdin>/);
  }
});

test('missing browser is an operational error rather than an invalid diagram', () => {
  const result = spawnSync(process.execPath, [cli], {
    input: 'flowchart TD\nA --> B', encoding: 'utf8', timeout: 30_000,
    env: { ...process.env, PUPPETEER_EXECUTABLE_PATH: '/does/not/exist' },
  });
  assert.equal(result.status, 2);
  assert.match(result.stderr, /ERROR mermaid-check/);
  assert.doesNotMatch(result.stdout, /PASS/);
});

test('maxTextSize cannot silently replace a large diagram with a fallback', renderOptions, t => {
  const path = fixture(t, {
    'small.json': '{"maxTextSize":20}',
    'large.json': '{"maxTextSize":1000}',
  });
  const text = 'flowchart TD\nA[Long enough to exceed the configured input limit] --> B';
  const small = run(['--config', path('small.json')], text);
  assert.equal(small.status, 1);
  assert.match(small.stderr, /exceeds maxTextSize/);
  assert.equal(run(['--config', path('large.json')], text).status, 0);
});

test('Pele mode renders, labels its scope, rejects errors, and accepts limit overrides', t => {
  const good = run(['--mode', 'pele'], 'stateDiagram-v2\n[*] --> Idle');
  assert.equal(good.status, 0, good.stderr);
  assert.match(good.stdout, /not Mermaid compatibility/);
  assert.equal(run(['--mode', 'pele'], 'flowchart TD\nA[unclosed').status, 1);
  const path = fixture(t, { 'limits.json': '{"limit":5}' });
  const limited = run(['--mode', 'pele', '--pele-options', path('limits.json')], 'flowchart TD\nA --> B');
  assert.equal(limited.status, 1);
  assert.match(limited.stderr, /limit/i);
});

test('help and engine versions work without a browser or stdin', () => {
  const help = run(['--help']);
  assert.equal(help.status, 0);
  assert.match(help.stdout, /Usage: mermaid-check/);
  const version = run(['--version']);
  assert.equal(version.status, 0);
  assert.match(version.stdout, /Mermaid 12\.2\.0.*Pele 1\.9\.0/);
});
