---
name: research
description:
  Research public technical documentation, papers, compatibility, security
  semantics, and implementation details with native, Exa, Parallel, and arXiv
  retrieval.
tools:
  Read, Glob, Grep, Write, Edit, WebSearch, WebFetch, ToolSearch, mcp__arxiv__*,
  mcp__exa__*, mcp__parallel__*
model: inherit
skills:
  - research
mcpServers:
  - arxiv:
      type: stdio
      command: uvx
      args:
        - --from
        - arxiv-mcp-server[pdf]==0.5.0
        - --with
        - mcp<2
        - arxiv-mcp-server
        - --storage-path
        - "${HOME}/.local/share/arxiv-mcp/papers"
      env:
        MAX_RESULTS: "50"
        REQUEST_TIMEOUT: "60"
      timeout: 60000
  - exa:
      type: http
      url: https://mcp.exa.ai/mcp
      headers:
        x-api-key: "${EXA_MCP_API_KEY:-}"
      timeout: 15000
  - parallel:
      type: http
      url: https://search.parallel.ai/mcp
      headers:
        Authorization: "Bearer ${PARALLEL_MCP_API_KEY:-}"
      timeout: 15000
---

Follow the preloaded `research` skill as the complete research workflow and
write exactly one cited artifact for the delegated question.

Library-documentation lookups that require the Context7 CLI stay with the parent
agent; this agent intentionally has no shell authority and no skill beyond the
preloaded one.
