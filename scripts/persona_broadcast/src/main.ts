import { readFile } from 'node:fs/promises';
import { startServer } from './server.js';
import { parseConfig } from './config.js';

const configPath = process.argv[2] ?? new URL('../config.example.json', import.meta.url);
const app = await startServer(parseConfig(JSON.parse(await readFile(configPath, 'utf8'))));
console.log(JSON.stringify({ event: 'listening', endpoint: app.url, pid: process.pid }));
let stopping = false;
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => {
  if (stopping) return;
  stopping = true;
  void app.close().then(() => { process.exitCode = 0; });
});
