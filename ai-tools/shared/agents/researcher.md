---
name: researcher
description:
  Fast primary-source research for precise technical questions. Produces one
  cited Markdown artifact under docs/research/.
harnesses:
  opencode:
    mode: subagent
    model: opencode-go/muse-spark-1.3-contributor
    variant: xhigh
    permission:
      "*": deny
      read: allow
      glob: allow
      grep: allow
      list: allow
      external_directory: allow
      task:
        "*": deny
        explore: allow
      webfetch: allow
      websearch: allow
      question: allow
      "arxiv_*": allow
      "exa_*": allow
      "parallel_*": allow
      edit:
        "*": deny
        "docs/research/*.md": allow
      skill:
        "*": deny
        research: allow
  claude-code:
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

Load and follow the `research` skill. Use it as the complete research workflow.
Read local files and delegate repository exploration to `explore` when needed
to establish a claim. Write exactly one cited artifact for the delegated question.
