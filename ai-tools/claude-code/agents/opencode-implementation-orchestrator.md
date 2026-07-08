---
name: opencode-implementation-orchestrator
description: Use for implementing design docs via opencode. The task should include a path to a .md design doc.
tools: mcp__opencode_bridge__opencode_implement_design
model: inherit
permissionMode: auto
mcpServers:
  - opencode_bridge:
      type: stdio
      command: bash
      args:
        - -lc
        - exec node "$HOME/dotfiles/ai-tools/claude-code/mcp/opencode-bridge.mjs"
      env:
        OPENCODE_BRIDGE_DEFAULT_MODEL: "openai/gpt-5.5"
        OPENCODE_BRIDGE_DEFAULT_VARIANT: "xhigh"
        OPENCODE_BRIDGE_TIMEOUT_MS: "1800000"
        OPENCODE_BRIDGE_MAX_OUTPUT_CHARS: "160000"
      timeout: 1800000
---

Use `mcp__opencode_bridge__opencode_implement_design` for design docs. Pass the `.md` path as `design_path`. If the user specifies an opencode agent or model, pass it through exactly. Return the result.
