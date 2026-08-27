import { mkdir, mkdtemp } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { beforeEach, describe, expect, test, vi } from "vitest"

const languageModel = vi.fn()
const createClaudeCode = vi.fn(() => ({ languageModel }))

vi.mock("ai-sdk-provider-claude-code", () => ({ createClaudeCode }))

describe("provider boundary", () => {
  beforeEach(() => {
    createClaudeCode.mockClear()
    languageModel.mockReset()
    vi.unstubAllEnvs()
  })

  test("requires and strips the auth sentinel", async () => {
    const { createOpenCodeClaudeAgent } = await import("../src/provider.js")

    expect(() =>
      createOpenCodeClaudeAgent({ name: "claude-agent", apiKey: "wrong" }),
    ).toThrow("authentication sentinel")

    createOpenCodeClaudeAgent({
      name: "claude-agent",
      apiKey: "claude-agent-cli-authenticated",
      authenticated: true,
    })

    expect(createClaudeCode).not.toHaveBeenCalled()
  })

  test("creates a fresh inner model for every call", async () => {
    const doStream = vi.fn().mockImplementation(async () => ({
      stream: new ReadableStream({ start: (controller) => controller.close() }),
    }))
    languageModel.mockImplementation(() => ({ doStream }))

    const { createOpenCodeClaudeAgent } = await import("../src/provider.js")
    const provider = createOpenCodeClaudeAgent({
      name: "claude-agent",
      apiKey: "claude-agent-cli-authenticated",
      authenticated: true,
    })
    const model = provider.languageModel("fable")
    const repository = await mkdtemp(join(tmpdir(), "claude-agent-provider-"))
    await mkdir(join(repository, ".git"))
    const options = {
      prompt: [{ role: "system", content: "Review systems." }],
      headers: {
        "x-opencode-agent": "reviewer-systems-fable",
        "x-opencode-directory": repository,
      },
      providerOptions: { "claude-agent": { effort: "high" } },
    }

    const first = await model.doStream(options as never)
    await first.stream.pipeTo(new WritableStream())
    const second = await model.doStream(options as never)
    await second.stream.pipeTo(new WritableStream())

    expect(createClaudeCode).toHaveBeenCalledTimes(2)
    expect(languageModel).toHaveBeenCalledTimes(2)
    expect(createClaudeCode.mock.calls[0]?.[0]).toBeUndefined()
  })

  test("passes only fixed, isolated SDK settings", async () => {
    const doGenerate = vi.fn().mockResolvedValue({ text: "review" })
    languageModel.mockImplementation(() => ({ doGenerate }))
    vi.stubEnv("ANTHROPIC_CANARY_TEST", "secret")

    const { createOpenCodeClaudeAgent } = await import("../src/provider.js")
    const provider = createOpenCodeClaudeAgent({
      name: "claude-agent",
      apiKey: "claude-agent-cli-authenticated",
      authenticated: true,
    })
    const repository = await mkdtemp(join(tmpdir(), "claude-agent-settings-"))
    await mkdir(join(repository, ".git"))
    const model = provider.languageModel("fable")

    await model.doGenerate({
      prompt: [
        { role: "system", content: "Review systems." },
        { role: "user", content: [{ type: "text", text: "Inspect this." }] },
      ],
      headers: {
        "x-opencode-agent": "reviewer-systems-fable",
        "x-opencode-directory": repository,
      },
      providerOptions: { "claude-agent": { effort: "xhigh" } },
    } as never)

    const [modelId, settings] = languageModel.mock.calls[0] as unknown as [
      string,
      Record<string, any>,
    ]
    expect(modelId).toBe("fable")
    expect(settings).toMatchObject({
      cwd: repository,
      effort: "xhigh",
      maxTurns: 16,
      maxBudgetUsd: 5,
      tools: ["Read", "Glob", "Grep", "WebSearch"],
      allowedTools: ["Read", "Glob", "Grep", "WebSearch"],
      permissionMode: "dontAsk",
      settingSources: [],
      skills: [],
      plugins: [],
      mcpServers: {},
      strictMcpConfig: true,
      persistSession: false,
      settings: {
        autoMemoryEnabled: false,
        disableClaudeAiConnectors: true,
        enableAllProjectMcpServers: false,
      },
    })
    expect(settings.disallowedTools).toContain("WebFetch")
    expect(settings.systemPrompt.append).toBe("Review systems.")
    expect(settings.env.ANTHROPIC_CANARY_TEST).toBeUndefined()
    expect(settings.env.CLAUDE_AGENT_SDK_CLIENT_APP).toBe(
      "opencode-claude-agent/0.1.0",
    )
    expect(doGenerate.mock.calls[0]?.[0].prompt).toEqual([
      { role: "user", content: [{ type: "text", text: "Inspect this." }] },
    ])
    expect(doGenerate.mock.calls[0]?.[0].headers).toBeUndefined()
    expect(doGenerate.mock.calls[0]?.[0].providerOptions).toBeUndefined()
  })

  test("rejects unsupported models, efforts, and OpenCode tools", async () => {
    const { createOpenCodeClaudeAgent } = await import("../src/provider.js")
    const provider = createOpenCodeClaudeAgent({
      apiKey: "claude-agent-cli-authenticated",
      authenticated: true,
    })
    expect(() => provider.languageModel("sonnet")).toThrow("Unsupported")

    const repository = await mkdtemp(join(tmpdir(), "claude-agent-reject-"))
    await mkdir(join(repository, ".git"))
    const model = provider.languageModel("fable")
    const base = {
      prompt: [{ role: "user", content: "Inspect this." }],
      headers: {
        "x-opencode-agent": "reviewer-systems-fable",
        "x-opencode-directory": repository,
      },
    }

    await expect(
      model.doGenerate({ ...base, tools: [{ type: "function", name: "read" }] } as never),
    ).rejects.toThrow("tool declarations")
    await expect(
      model.doGenerate({
        ...base,
        providerOptions: { "claude-agent": { effort: "medium" } },
      } as never),
    ).rejects.toThrow("unsupported effort")
  })
})
