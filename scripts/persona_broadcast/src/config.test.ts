import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseConfig } from './config.js';

const base = { port: 47835, deadlineMs: 2500, cleanupMs: 300, devices: [{ id: 'windows', endpoint: 'http://127.0.0.1:47832/mcp' }] };
test('operator configuration rejects recursive routes, duplicate destinations and invalid private endpoints', () => {
  assert.equal(parseConfig(base).maxConcurrentPerDevice, 4);
  for (const endpoint of ['http://127.0.0.1:47835/mcp', 'https://evil.example/mcp', 'http://user:pass@127.0.0.1:47832/mcp', 'http://127.0.0.1:47832/mcp?target=x', 'http://127.0.0.1:47832/other']) {
    assert.throws(() => parseConfig({ ...base, devices: [{ id: 'bad', endpoint }] }));
  }
  assert.throws(() => parseConfig({ ...base, devices: [base.devices[0], base.devices[0]] }));
  for (const deadlineMs of [0, -1, Infinity, 2_147_483_648]) assert.throws(() => parseConfig({ ...base, deadlineMs }));
  assert.equal(parseConfig({ ...base, maxConcurrentPerDevice: 12 }).maxConcurrentPerDevice, 12, 'the operator may override recommended session admission');
});
