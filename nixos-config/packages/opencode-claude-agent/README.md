# Claude Agent reviewer provider

This provider runs native OpenCode reviewer subagents through the Claude Agent SDK using the existing Claude login. Callers use the normal `subagent` tool; they do not need a special prompt or command.

The SDK receives a short read-only reviewer system prompt. Each original OpenCode system message is preserved verbatim as labeled application instructions at its original position in task history. These instructions have task-context priority, not SDK system priority. Conflicting requests or long histories can therefore weaken instruction following. No text is filtered by wording.

The pinned inner adapter flattens history into task text. It can omit assistant-embedded SDK tool results on followup, so the reviewer may need to read those files again. This conversion does not provide lossless native conversation replay.

The provider uses streaming input for SDK hooks and native tools. Authentication checks, model restrictions, tool-declaration rejection, and workspace path guards remain enforced by code. Available SDK tools are Read, Glob, Grep, and WebSearch. Read-only does not mean network-isolated: permitted file content is not protected from disclosure in a search query or review response.

Each request uses a fresh SDK session with an explicit title, a 16-turn limit, a $5 SDK list-price budget, and a ten-minute timeout. The SDK chooses the output-token allowance; the provider does not apply an artificial small cap. SDK cost estimates do not establish an actual account charge. Failures propagate to the caller without a model fallback.

Run `npm ci --ignore-scripts`, `npm test`, `npm run typecheck`, and `npm run build` to verify the package. Tests mock the SDK boundary and make no model requests.
