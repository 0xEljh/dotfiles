import { createServer } from 'node:http';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import * as z from 'zod/v4';
import { createBroadcaster } from './broadcast.js';
import { parseConfig, type Config } from './config.js';
export type { Config } from './config.js';

function mcpServer(broadcaster: ReturnType<typeof createBroadcaster>, disconnected: AbortSignal) {
  const server = new McpServer({ name: 'Persona Broadcast', version: '1.0.0' }, { instructions:
    'One interface controls every configured Persona desktop. Never choose a device. Call list_animations for live actions; play_animation broadcasts an action wherever installed. control_window broadcasts show, hide, or independent toggles. Reads report each desktop separately. Partial success is expected when a desktop sleeps. Never automatically retry a partial or uncertain control: some devices may already have acted. Nothing is queued or replayed after recovery. Persona does not speak or play audio.',
  });
  const tools = {
    get_status: { description: 'Read all Persona desktops, including individual window states and availability.', schema: z.object({}).strict(), readOnly: true },
    list_animations: { description: 'Read live action catalogs from every reachable desktop. Use any discovered action name; playback is best effort where installed.', schema: z.object({}).strict(), readOnly: true },
    play_animation: { description: 'Broadcast one installed body-animation action to all desktops, showing each participating model. No audio. Do not retry partial or unknown delivery automatically.',
      schema: z.object({ animation: z.string().regex(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/).max(48) }).strict(), readOnly: false },
    control_window: { description: 'Broadcast show, hide, or toggle to every desktop. Toggle flips each current state independently; show/hide requests a common state. Hiding does not quit.',
      schema: z.object({ action: z.enum(['show', 'hide', 'toggle']) }).strict(), readOnly: false },
  };
  for (const [name, tool] of Object.entries(tools)) {
    server.registerTool(name, { description: tool.description, inputSchema: tool.schema,
      annotations: { readOnlyHint: tool.readOnly, destructiveHint: false, idempotentHint: tool.readOnly, openWorldHint: false },
    }, async (args: Record<string, unknown>, extra: { signal: AbortSignal }) => broadcaster.call(name, args, AbortSignal.any([extra.signal, disconnected])));
  }
  return server;
}

export async function startServer(input: Config) {
  const config = parseConfig(input);
  const broadcaster = createBroadcaster(config);
  const active = new Set<McpServer>();
  let port = config.port;
  const http = createServer(async (request, response) => {
    const send = (status: number, message: string) => { response.writeHead(status, { 'content-type': 'application/json' }); response.end(JSON.stringify({ error: message })); };
    const authority = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
    if (!authority.has(request.headers.host ?? '')) { send(403, 'Untrusted Host'); return; }
    if (request.headers.origin !== undefined && ![...authority].map(host => `http://${host}`).includes(request.headers.origin)) { send(403, 'Untrusted Origin'); return; }
    if (request.url === '/health' && request.method === 'GET') { send(200, 'Broadcast process is running; desktop availability is not checked.'); return; }
    if (request.url !== '/mcp') { send(404, 'Not found'); return; }
    if (request.method !== 'POST') { response.setHeader('allow', 'POST'); send(405, 'Stateless MCP accepts POST only'); return; }
    if (request.headers['content-type']?.split(';')[0]?.trim() !== 'application/json') { send(415, 'Expected application/json'); return; }
    let body: unknown;
    try {
      const chunks: Buffer[] = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 65536) { send(413, 'Request body exceeds 64 KiB'); return; }
        chunks.push(chunk);
      }
      body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch { if (!response.headersSent) send(400, 'Invalid JSON'); return; }
    const disconnected = new AbortController();
    const server = mcpServer(broadcaster, disconnected.signal);
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    active.add(server);
    response.once('close', () => { disconnected.abort(); active.delete(server); void server.close(); });
    try { await server.connect(transport); await transport.handleRequest(request, response, body); }
    catch { if (!response.headersSent) send(500, 'MCP request failed'); }
  });
  http.requestTimeout = 10_000;
  http.headersTimeout = 10_000;
  await new Promise<void>((resolve, reject) => { http.once('error', reject); http.listen(port, '127.0.0.1', resolve); });
  const address = http.address();
  if (!address || typeof address === 'string') throw new Error('No TCP listener');
  port = address.port;
  if (config.devices.some(device => Number(new URL(device.endpoint).port) === port)) {
    http.close(); throw new Error('A device cannot point at the broadcast listener');
  }
  return { http, url: `http://127.0.0.1:${port}/mcp`, async close() {
    await broadcaster.close();
    await Promise.allSettled([...active].map(server => server.close()));
    http.closeAllConnections();
    await new Promise<void>(resolve => http.close(() => resolve()));
  } };
}
