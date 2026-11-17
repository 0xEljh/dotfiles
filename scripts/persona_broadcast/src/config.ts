import * as z from 'zod/v4';

const milliseconds = z.number().int().min(1).max(2_147_483_647);
const schema = z.object({
  port: z.number().int().min(0).max(65535),
  deadlineMs: milliseconds,
  cleanupMs: milliseconds,
  maxConcurrentPerDevice: z.number().int().positive().default(4),
  devices: z.array(z.object({
    id: z.string().regex(/^[a-z][a-z0-9-]{0,63}$/),
    endpoint: z.string().url(),
  }).strict()).min(1),
}).strict();

export type Config = z.infer<typeof schema>;

export function parseConfig(input: unknown): Config {
  const config = schema.parse(input);
  const ids = new Set<string>(), endpoints = new Set<string>();
  for (const device of config.devices) {
    const url = new URL(device.endpoint);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/mcp'
      || !url.port || url.username || url.password || url.search || url.hash) {
      throw new Error('Device endpoints must be http://127.0.0.1:PORT/mcp without credentials or query parameters');
    }
    if (Number(url.port) === config.port) throw new Error('A device cannot point at the broadcast listener');
    if (ids.has(device.id) || endpoints.has(url.href)) throw new Error('Device IDs and endpoints must be unique');
    ids.add(device.id); endpoints.add(url.href);
  }
  return config;
}
