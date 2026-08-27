import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
} from "@ai-sdk/provider"
import { join } from "node:path"
import {
  createClaudeCode,
  type ClaudeCodeSettings,
} from "ai-sdk-provider-claude-code"

import { admission, wrapStream } from "./limits.js"
import {
  assertAllowedAgent,
  assertSafeGrepPath,
  assertSafeGlobPattern,
  assertSafeToolPath,
  AUTH_SENTINEL,
  resolveWorkspace,
  sanitizeClaudeEnvironment,
  globLiteralPrefix,
  splitSystemPrompt,
} from "./policy.js"

const ALLOWED_TOOLS = ["Read", "Glob", "Grep", "WebSearch"]
const DISALLOWED_TOOLS = [
  "Agent",
  "AskUserQuestion",
  "Bash",
  "Edit",
  "NotebookEdit",
  "Skill",
  "Task",
  "TaskCreate",
  "TaskUpdate",
  "WebFetch",
  "Write",
]
const QUERY_TIMEOUT_MS = 10 * 60 * 1000
const MAX_HISTORY_BYTES = 4 * 1024 * 1024

type FactoryOptions = {
  name?: string
  apiKey?: string
  authenticated?: boolean
  unavailableReason?: string
}

function header(options: LanguageModelV3CallOptions, name: string): string | undefined {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(options.headers ?? {})) {
    if (key.toLowerCase() === wanted) return value
  }
  return undefined
}

function effort(options: LanguageModelV3CallOptions, provider: string): "high" | "xhigh" {
  const value = options.providerOptions?.[provider]?.effort
  if (value === undefined || value === "high") return "high"
  if (value === "xhigh") return "xhigh"
  throw new Error(`Claude Agent rejected unsupported effort: ${String(value)}`)
}

function pathFromTool(toolName: string, rawInput: unknown): string | undefined {
  if (!rawInput || typeof rawInput !== "object") return undefined
  const input = rawInput as Record<string, unknown>
  if (toolName === "Read") {
    return typeof input.file_path === "string" ? input.file_path : undefined
  }
  if (toolName === "Glob" || toolName === "Grep") {
    return typeof input.path === "string" ? input.path : "."
  }
  return undefined
}

function workspaceHook(workspace: string) {
  return async (input: unknown) => {
    if (!input || typeof input !== "object") return {}
    const record = input as Record<string, unknown>
    const toolName = typeof record.tool_name === "string" ? record.tool_name : ""
    const toolInput = record.tool_input
    let globPrefix: string | undefined
    if (toolName === "Glob" && toolInput && typeof toolInput === "object") {
      const pattern = (toolInput as Record<string, unknown>).pattern
      try {
        const value = typeof pattern === "string" ? pattern : ""
        assertSafeGlobPattern(value)
        globPrefix = globLiteralPrefix(value)
      } catch (error) {
        return {
          hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason:
              error instanceof Error ? error.message : "Unsafe glob pattern",
          },
        }
      }
    }
    const requestedPath = pathFromTool(toolName, toolInput)
    if (!requestedPath) {
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: `Missing path for ${toolName}`,
        },
      }
    }
    try {
      if (toolName === "Grep") {
        await assertSafeGrepPath(workspace, requestedPath)
      } else {
        await assertSafeToolPath(workspace, requestedPath)
      }
      if (globPrefix && globPrefix !== ".") {
        await assertSafeToolPath(workspace, join(requestedPath, globPrefix))
      }
      return {}
    } catch (error) {
      return {
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason:
            error instanceof Error ? error.message : "Unsafe path",
        },
      }
    }
  }
}

function combinedAbortSignal(signal: AbortSignal | undefined): AbortSignal {
  const timeout = AbortSignal.timeout(QUERY_TIMEOUT_MS)
  return signal ? AbortSignal.any([signal, timeout]) : timeout
}

async function requestContext(
  options: LanguageModelV3CallOptions,
  provider: string,
): Promise<{
  options: LanguageModelV3CallOptions
  settings: ClaudeCodeSettings
}> {
  if (options.tools && options.tools.length > 0) {
    throw new Error("Claude Agent does not accept OpenCode tool declarations")
  }
  if (Buffer.byteLength(JSON.stringify(options.prompt), "utf8") > MAX_HISTORY_BYTES) {
    throw new Error("Claude Agent history exceeds 4 MiB")
  }

  assertAllowedAgent(header(options, "x-opencode-agent"))
  const workspace = await resolveWorkspace(
    header(options, "x-opencode-directory") ?? "",
  )
  const split = splitSystemPrompt(options.prompt)
  const environment = sanitizeClaudeEnvironment(process.env)

  const settings: ClaudeCodeSettings = {
    cwd: workspace,
    systemPrompt: {
      type: "preset",
      preset: "claude_code",
      append: split.systemPrompt,
    },
    effort: effort(options, provider),
    thinking: { type: "adaptive" },
    maxTurns: 16,
    maxBudgetUsd: 5,
    tools: ALLOWED_TOOLS,
    allowedTools: ALLOWED_TOOLS,
    disallowedTools: DISALLOWED_TOOLS,
    permissionMode: "dontAsk",
    settingSources: [],
    skills: [],
    plugins: [],
    mcpServers: {},
    strictMcpConfig: true,
    persistSession: false,
    promptSuggestions: false,
    enableFileCheckpointing: false,
    logger: false,
    env: environment,
    hooks: {
      PreToolUse: [
        {
          matcher: "Read|Glob|Grep",
          hooks: [workspaceHook(workspace)],
        },
      ],
    },
    settings: {
      autoMemoryEnabled: false,
      disableClaudeAiConnectors: true,
      enableAllProjectMcpServers: false,
    },
  }

  return {
    settings,
    options: {
      ...options,
      prompt: split.prompt,
      abortSignal: combinedAbortSignal(options.abortSignal),
      headers: undefined,
      providerOptions: undefined,
      tools: undefined,
      toolChoice: undefined,
    },
  }
}

class ClaudeAgentModel implements LanguageModelV3 {
  readonly specificationVersion = "v3" as const
  readonly provider: string
  readonly modelId: string
  readonly supportedUrls = {}

  constructor(provider: string, modelId: string) {
    if (modelId !== "fable") throw new Error(`Unsupported Claude Agent model: ${modelId}`)
    this.provider = provider
    this.modelId = modelId
  }

  async doGenerate(options: LanguageModelV3CallOptions) {
    const release = await admission.acquire(options.abortSignal)
    try {
      const request = await requestContext(options, this.provider)
      const inner = createClaudeCode().languageModel("fable", request.settings)
      return await inner.doGenerate(request.options)
    } finally {
      release()
    }
  }

  async doStream(options: LanguageModelV3CallOptions) {
    const release = await admission.acquire(options.abortSignal)
    try {
      const request = await requestContext(options, this.provider)
      const inner = createClaudeCode().languageModel("fable", request.settings)
      const result = await inner.doStream(request.options)
      return {
        ...result,
        stream: wrapStream(result.stream, release, request.options.abortSignal),
      }
    } catch (error) {
      release()
      throw error
    }
  }
}

export function createOpenCodeClaudeAgent(options: FactoryOptions = {}) {
  if (options.apiKey !== AUTH_SENTINEL || options.authenticated !== true) {
    // The auth loader reports why it declined, so the operator sees the real
    // cause here rather than a sentinel message that names the wrong problem.
    throw new Error(
      options.unavailableReason ?? "Claude Agent authentication sentinel is missing",
    )
  }
  const provider = options.name ?? "claude-agent"
  return {
    languageModel(modelId: string): LanguageModelV3 {
      return new ClaudeAgentModel(provider, modelId)
    },
  }
}
