import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { once } from 'node:events';
import { test, type TestContext } from 'node:test';
import { setTimeout as sleep } from 'node:timers/promises';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import type { CallToolResult } from '@modelcontextprotocol/sdk/types.js';
import { startServer, type Config } from './server.js';

type Mode = 'ok' | 'hang' | 'reject' | 'lost-ack' | 'hang-initialized' | 'slow-initialize' | 'hang-cleanup';
async function waitFor(check: () => boolean) {
  const deadline = Date.now() + 1000;
  while (!check() && Date.now() < deadline) await sleep(5);
  assert.ok(check(), 'fixture condition must occur within one second');
}
async function device(label: string, initial: Mode = 'ok', jitter = false) {
  let mode = initial;
  const calls: { name: string; arguments: unknown; at: number }[] = [];
  const sessions = new Set<string>();
  let sequence = 0;
  let peakSessions = 0;
  let visible = true;
  const http = createServer(async (req, res) => {
    if (req.method === 'DELETE') {
      if (mode === 'hang-cleanup') return;
      sessions.delete(String(req.headers['mcp-session-id'])); res.writeHead(200).end(); return;
    }
    if (req.method !== 'POST') { res.writeHead(405).end(); return; }
    const chunks = []; for await (const c of req) chunks.push(c);
    const body = JSON.parse(Buffer.concat(chunks).toString());
    if (jitter) await sleep(Math.floor(Math.random() * 15));
    if (body.method === 'initialize') {
      if (mode === 'slow-initialize') await sleep(400);
      if (sessions.size >= 8) sessions.delete(sessions.values().next().value!);
      const id = `${label}-${++sequence}`; sessions.add(id);
      peakSessions = Math.max(peakSessions, sessions.size);
      res.setHeader('mcp-session-id', id);
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: {
        protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: label, version: '1' },
      } })); return;
    }
    if (!sessions.has(String(req.headers['mcp-session-id']))) { res.writeHead(404).end(); return; }
    if (!('id' in body)) {
      if (body.method === 'notifications/initialized' && mode === 'hang-initialized') return;
      res.writeHead(202).end(); return;
    }
    calls.push({ ...body.params, at: Date.now() });
    if (mode === 'hang' || mode === 'lost-ack') return;
    const { name, arguments: args } = body.params;
    if (name === 'control_window') visible = args.action === 'toggle' ? !visible : args.action === 'show';
    const text = name === 'get_status' ? JSON.stringify({ modelConfigured: true, windowVisible: visible, label })
      : name === 'list_animations' ? `- idle: ${label} idle\n- ${label}-motion: device-specific action`
      : `${label}: ${name} acknowledged`;
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ jsonrpc: '2.0', id: body.id, result: {
      content: [{ type: 'text', text: mode === 'reject' ? 'Permission denied by device' : text }],
      ...(mode === 'reject' ? { isError: true } : {}),
    } }));
  });
  http.listen(0, '127.0.0.1'); await once(http, 'listening');
  const address = http.address(); assert.ok(address && typeof address !== 'string');
  return { endpoint: `http://127.0.0.1:${address.port}/mcp`, calls, sessions,
    get peakSessions() { return peakSessions; },
    mode(value: Mode) { mode = value; },
    async close() { http.closeAllConnections(); await new Promise<void>(r => http.close(() => r())); },
  };
}

async function setup(t: TestContext, modes: [Mode, Mode] = ['ok', 'ok'], jitter = false) {
  const a = await device('a', modes[0], jitter), b = await device('b', modes[1], jitter);
  t.after(async () => { await a.close(); await b.close(); });
  const config: Config = { port: 0, deadlineMs: 250, cleanupMs: 100, maxConcurrentPerDevice: 4,
    devices: [{ id: 'a', endpoint: a.endpoint }, { id: 'b', endpoint: b.endpoint }] };
  const server = await startServer(config);
  t.after(() => server.close());
  const client = new Client({ name: 'contract-test', version: '1' });
  await client.connect(new StreamableHTTPClientTransport(new URL(server.url)));
  t.after(() => client.close());
  const call = async (name: string, args: Record<string, unknown> = {}) => client.callTool({ name, arguments: args }) as Promise<CallToolResult>;
  const data = (r: CallToolResult) => JSON.parse((r.content[0] as { text: string }).text);
  return { a, b, server, client, call, data };
}

test('one interface broadcasts once to both devices, preserves catalogs, and cleans backend sessions', async t => {
  const { a, b, client, call, data } = await setup(t);
  assert.deepEqual((await client.listTools()).tools.map(x => x.name).sort(),
    ['control_window', 'get_status', 'list_animations', 'play_animation']);
  const result = await call('play_animation', { animation: 'idle' });
  assert.equal(data(result).outcome, 'complete'); assert.notEqual(result.isError, true);
  for (const fixture of [a, b]) {
    assert.deepEqual(fixture.calls.map(x => [x.name, x.arguments]), [['play_animation', { animation: 'idle' }]]);
    assert.equal(fixture.sessions.size, 0);
  }
  const catalog = data(await call('list_animations'));
  assert.match(JSON.stringify(catalog), /a-motion/); assert.match(JSON.stringify(catalog), /b-motion/);
  await call('control_window', { action: 'hide' });
  const status = data(await call('get_status'));
  for (const result of status.devices) assert.equal(JSON.parse(result.result.content[0].text).windowVisible, false);
});

test('hanging desktop yields bounded partial success, no replay, then recovers on the next new request', async t => {
  const { a, b, call, data } = await setup(t, ['ok', 'lost-ack']);
  const start = Date.now();
  const result = await call('play_animation', { animation: 'speaking' });
  assert.equal(data(result).outcome, 'partial'); assert.notEqual(result.isError, true);
  assert.equal(data(result).devices[1].status, 'unknown');
  assert.ok(Date.now() - start < 1000, 'whole call must be bounded');
  assert.ok(a.calls[0]!.at - start < 200, 'healthy dispatch must not wait for offline deadline');
  assert.equal(a.calls.length, 1); assert.equal(b.calls.length, 1);
  assert.equal(a.sessions.size, 0); assert.equal(b.sessions.size, 0);
  b.mode('ok'); await sleep(100);
  assert.equal(b.calls.length, 1, 'recovery alone must not replay old commands');
  assert.equal(data(await call('play_animation', { animation: 'idle' })).outcome, 'complete');
  assert.equal(a.calls.length, 2); assert.equal(b.calls.length, 2);
});

test('hung handshake is bounded and does not dispatch controls on the uninitialized backend', async t => {
  const { a, b, call, data } = await setup(t, ['ok', 'hang-initialized']);
  const result = data(await call('play_animation', { animation: 'idle' }));
  assert.equal(result.outcome, 'partial'); assert.equal(result.devices[1].status, 'unavailable');
  assert.equal(a.calls.length, 1); assert.equal(b.calls.length, 0); assert.equal(b.sessions.size, 0);
});

test('a stalled first device does not serialize or suppress dispatch to the second', async t => {
  const { b, call, data } = await setup(t, ['hang', 'ok']);
  const before = Date.now();
  const result = data(await call('play_animation', { animation: 'idle' }));
  assert.equal(result.outcome, 'partial'); assert.equal(result.devices[1].status, 'ok');
  assert.ok(b.calls[0]!.at - before < 200);
});

test('initialization completing after the deadline never triggers a late control', async t => {
  const { b, call, data } = await setup(t, ['ok', 'slow-initialize']);
  assert.equal(data(await call('play_animation', { animation: 'idle' })).devices[1].status, 'unavailable');
  await sleep(450);
  assert.equal(b.calls.length, 0);
});

test('unconfirmed cleanup is bounded and does not erase an acknowledged control', async t => {
  const { call, data } = await setup(t, ['ok', 'hang-cleanup']);
  const before = Date.now();
  const result = data(await call('play_animation', { animation: 'idle' }));
  assert.equal(result.outcome, 'complete');
  assert.equal(result.devices[1].cleanup, 'unconfirmed');
  assert.ok(Date.now() - before < 750);
});

test('backend rejection is explicit and all failed returns an MCP tool error', async t => {
  const { a, b, call, data } = await setup(t, ['ok', 'reject']);
  assert.equal(data(await call('control_window', { action: 'show' })).devices[1].status, 'rejected');
  a.mode('reject');
  const result = await call('play_animation', { animation: 'idle' });
  assert.equal(data(result).outcome, 'failed'); assert.equal(result.isError, true);
  assert.equal(b.calls.length, 2);
});

test('zero acknowledged controls with possible delivery is uncertain, not a retryable failure; reads stay unavailable', async t => {
  const { call, data } = await setup(t, ['lost-ack', 'lost-ack']);
  const control = await call('control_window', { action: 'toggle' });
  assert.equal(data(control).outcome, 'uncertain');
  assert.equal(data(control).uncertain, true);
  assert.match(data(control).note, /[Dd]o not retry/);
  const read = data(await call('get_status'));
  assert.equal(read.outcome, 'failed');
  assert.equal(read.uncertain, false);
  assert.ok(read.devices.every((d: { status: string }) => d.status === 'unavailable'));
});

test('more than eight overlapping callers are refused before backend session admission, not queued', async t => {
  const { a, b, call, data } = await setup(t, ['hang', 'hang']);
  const results = await Promise.all(Array.from({ length: 12 }, () => call('play_animation', { animation: 'idle' })));
  assert.ok(results.some(r => data(r).devices.every((d: { error?: string }) => d.error?.includes('busy'))));
  assert.ok(a.peakSessions <= 4); assert.ok(b.peakSessions <= 4);
  assert.equal(a.calls.length, 4); assert.equal(b.calls.length, 4);
  assert.equal(a.sessions.size, 0); assert.equal(b.sessions.size, 0);
});

test('invalid interface requests never dispatch; HTTP boundary rejects bad Host, Origin and oversized bodies', async t => {
  const { a, b, server, call } = await setup(t);
  for (const [name, args] of [['play_animation', { animation: '../file' }], ['play_animation', { animation: '1invalid' }], ['control_window', { action: 'quit' }], ['generate_animation', { prompt: 'wave' }], ['get_status', { device: 'a' }]] as const) {
    let result: CallToolResult;
    try { result = await call(name, args); }
    catch (error) { assert.match(String(error), /tool|argument|validation/i); continue; }
    assert.equal(result.isError, true);
  }
  const hostileHost = await new Promise<number>(resolve => {
    const req = request(server.url, { method: 'POST', headers: { host: 'evil.example', 'content-type': 'application/json' } }, res => { res.resume(); resolve(res.statusCode!); });
    req.end('{}');
  });
  assert.equal(hostileHost, 403);
  assert.equal((await fetch(server.url, { method: 'POST', headers: { origin: 'https://evil.example' }, body: '{}' })).status, 403);
  assert.equal((await fetch(server.url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: ' '.repeat(70_000) })).status, 413);
  assert.equal((await fetch(server.url.replace('/mcp', '/missing'))).status, 404);
  assert.equal((await fetch(server.url)).status, 405);
  assert.equal(a.calls.length, 0); assert.equal(b.calls.length, 0);
});

test('jittered overlapping requests preserve each response and clean every backend session', async t => {
  const { a, b, call, data } = await setup(t, ['ok', 'ok'], true);
  for (let round = 0; round < 5; round++) {
    const results = await Promise.all(['idle', 'speaking', 'a-motion', 'b-motion'].map(animation => call('play_animation', { animation })));
    for (const r of results) assert.equal(data(r).outcome, 'complete');
  }
  assert.equal(a.calls.length, 20); assert.equal(b.calls.length, 20);
  assert.equal(a.sessions.size, 0); assert.equal(b.sessions.size, 0);
});

test('caller disconnect cancels local work, cleans known sessions, and never retries submitted controls', async t => {
  const { a, b, server } = await setup(t, ['hang', 'hang']);
  const controller = new AbortController();
  const pending = fetch(server.url, { method: 'POST', headers: {
    'content-type': 'application/json', accept: 'application/json, text/event-stream',
  }, body: JSON.stringify({ jsonrpc: '2.0', id: 999, method: 'tools/call', params: { name: 'play_animation', arguments: { animation: 'idle' } } }), signal: controller.signal });
  void pending.catch(() => {});
  await waitFor(() => a.calls.length > 0 && b.calls.length > 0);
  controller.abort(); await assert.rejects(pending);
  await sleep(150);
  assert.equal(a.calls.length, 1); assert.equal(b.calls.length, 1);
  assert.equal(a.sessions.size, 0); assert.equal(b.sessions.size, 0);
});

test('shutdown bounds active calls and releases known backend sessions without new dispatch', async t => {
  const { a, b, call, server } = await setup(t, ['hang', 'hang']);
  const pending = call('control_window', { action: 'toggle' });
  void pending.catch(() => {});
  await waitFor(() => a.calls.length > 0 && b.calls.length > 0);
  const before = Date.now(); await server.close();
  await pending.catch(() => {});
  assert.ok(Date.now() - before < 750);
  assert.equal(a.sessions.size, 0); assert.equal(b.sessions.size, 0);
  assert.equal(a.calls.length, 1); assert.equal(b.calls.length, 1);
});
