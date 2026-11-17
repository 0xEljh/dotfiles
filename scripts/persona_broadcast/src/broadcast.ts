import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import type { CallToolResult } from '@modelcontextprotocol/sdk/types.js';
import type { Config } from './config.js';

type Device = Config['devices'][number];
export type DeviceResult = {
  device: string;
  status: 'ok' | 'rejected' | 'unavailable' | 'unknown';
  result?: CallToolResult;
  error?: string;
  cleanup?: 'unconfirmed';
};

async function attempt(device: Device, name: string, args: Record<string, unknown>,
  config: Config, signal: AbortSignal): Promise<DeviceResult> {
  let submitted = false;
  const transport = new StreamableHTTPClientTransport(new URL(device.endpoint), {
    requestInit: { redirect: 'error' },
    reconnectionOptions: { maxRetries: 0, initialReconnectionDelay: 1000, maxReconnectionDelay: 1000, reconnectionDelayGrowFactor: 1 },
    fetch: (url, init) => {
      // These short-lived clients do not consume unsolicited event streams.
      if (init?.method === 'GET') return Promise.resolve(new Response(null, { status: 405 }));
      return fetch(url, { ...init, redirect: 'error', signal: AbortSignal.any([signal, ...(init?.signal ? [init.signal] : [])]) });
    },
  });
  const client = new Client({ name: 'persona-broadcast', version: '1.0.0' }, { capabilities: {} });
  const abort = () => { void transport.close(); };
  signal.addEventListener('abort', abort, { once: true });
  let outcome: DeviceResult;
  try {
    await client.connect(transport, { signal, timeout: config.deadlineMs, resetTimeoutOnProgress: false });
    signal.throwIfAborted();
    submitted = true;
    const result = await client.callTool({ name, arguments: args }, undefined,
      { signal, timeout: config.deadlineMs, resetTimeoutOnProgress: false }) as CallToolResult;
    outcome = { device: device.id, status: result.isError ? 'rejected' : 'ok', result };
  } catch {
    const control = name === 'play_animation' || name === 'control_window';
    outcome = { device: device.id, status: submitted && control ? 'unknown' : 'unavailable',
      error: signal.aborted ? 'Device deadline exceeded.' : 'Device MCP connection or request failed.',
    };
  } finally {
    signal.removeEventListener('abort', abort);
    await client.close().catch(() => {});
  }
  // SDK close() is local-only. DELETE needs a new budget even after a work timeout.
  if (transport.sessionId) {
    try {
      const response = await fetch(device.endpoint, { method: 'DELETE', redirect: 'error',
        headers: { 'mcp-session-id': transport.sessionId, ...(transport.protocolVersion ? { 'mcp-protocol-version': transport.protocolVersion } : {}) },
        signal: AbortSignal.timeout(config.cleanupMs),
      });
      void response.body?.cancel().catch(() => {});
      if (!response.ok && response.status !== 404) outcome.cleanup = 'unconfirmed';
    } catch { outcome.cleanup = 'unconfirmed'; }
  }
  return outcome;
}

export function createBroadcaster(config: Config) {
  const inFlight = new Map<string, number>();
  const pending = new Set<Promise<CallToolResult>>();
  const shutdown = new AbortController();
  async function broadcast(name: string, args: Record<string, unknown>, parent: AbortSignal): Promise<CallToolResult> {
    const signal = AbortSignal.any([shutdown.signal, parent, AbortSignal.timeout(config.deadlineMs)]);
    const devices = await Promise.all(config.devices.map(async device => {
      const count = inFlight.get(device.id) ?? 0;
      if (count >= config.maxConcurrentPerDevice) return { device: device.id, status: 'unavailable' as const, error: 'Device is busy; no request was dispatched.' };
      inFlight.set(device.id, count + 1);
      try { return await attempt(device, name, args, config, signal); }
      finally { inFlight.set(device.id, inFlight.get(device.id)! - 1); }
    }));
    const successes = devices.filter(device => device.status === 'ok').length;
    const uncertain = devices.some(device => device.status === 'unknown');
    const result = {
      outcome: successes === devices.length ? 'complete' : successes ? 'partial' : uncertain ? 'uncertain' : 'failed',
      uncertain,
      devices,
      note: 'Best effort. Acknowledgments do not prove rendered motion. Unknown delivery may already have executed, or may execute late. Do not retry an entire control to repair a partial or uncertain result; it can repeat devices that already acted. The gateway never retries or queues controls.',
    };
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result, isError: successes === 0 };
  }
  return {
    call(name: string, args: Record<string, unknown>, signal: AbortSignal) {
      const promise = broadcast(name, args, signal);
      pending.add(promise);
      void promise.finally(() => pending.delete(promise)).catch(() => {});
      return promise;
    },
    async close() { shutdown.abort(); await Promise.allSettled([...pending]); },
  };
}
