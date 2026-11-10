import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir, cpus } from 'node:os';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';

// Fresh CLI processes, with warm filesystem caches and no dependency installation.
const cli = fileURLToPath(new URL('./cli.mjs', import.meta.url));
const samples = [
  'flowchart TD\nA[Start] --> B{Ready?}\nB -->|yes| C[Done]\nB -->|no| A',
  'sequenceDiagram\nparticipant A as Alice\nparticipant B as Bob\nA->>B: Hello\nB-->>A: Hi',
  'classDiagram\nclass Animal {\n+String name\n+run()\n}\nAnimal <|-- Cat',
  'stateDiagram-v2\n[*] --> Idle\nIdle --> Running: start\nRunning --> Idle: stop',
  'erDiagram\nCUSTOMER ||--o{ ORDER : places',
  'gantt\ntitle Release\ndateFormat YYYY-MM-DD\nsection Work\nBuild :a1, 2026-11-01, 3d',
];
const dir = mkdtempSync(join(tmpdir(), 'mermaid-check-benchmark-'));
const files = Array.from({ length: 12 }, (_, i) => {
  const path = join(dir, `${i}.mmd`);
  writeFileSync(path, samples[i % samples.length]);
  return path;
});
console.log(`${process.version} ${process.platform}/${process.arch}; ${cpus()[0]?.model}`);
console.log(spawnSync(process.execPath, [cli, '--version'], { encoding: 'utf8' }).stdout.trim());
console.log('Five fresh processes per case, median wall time in milliseconds:');
try {
  for (const count of [1, 12]) {
    for (const mode of ['syntax', 'render', 'pele']) {
      const times = [];
      for (let run = 0; run < 5; run++) {
        const start = performance.now();
        const result = spawnSync(process.execPath, [cli, '--mode', mode, ...files.slice(0, count)], {
          encoding: 'utf8', timeout: 120_000,
        });
        if (result.status !== 0) throw new Error(`${mode}: ${result.error?.message ?? result.stderr}`);
        times.push(performance.now() - start);
      }
      times.sort((a, b) => a - b);
      console.log(`${mode}: ${count} diagram(s), ${times[2].toFixed(1)} ms (range ${times[0].toFixed(1)}–${times[4].toFixed(1)})`);
    }
  }
} finally { rmSync(dir, { recursive: true, force: true }); }
