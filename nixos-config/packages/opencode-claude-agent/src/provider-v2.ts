import { AUTH_SENTINEL } from "./policy.js"
import { createOpenCodeClaudeAgent } from "./provider.js"

// The dynamic AI SDK loader selects a named create* export. Keep exactly one.
// The session request hook separately checks that the subscription is usable.
export function createClaudeAgentV2(options: { name?: string; apiKey?: string } = {}) {
  return createOpenCodeClaudeAgent({
    name: options.name,
    apiKey: options.apiKey,
    authenticated: options.apiKey === AUTH_SENTINEL,
  })
}
