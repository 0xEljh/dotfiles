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

  test.each(["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"])("streams with a fresh inner model using the selected ID %s", async (selectedModel) => {
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
    const model = provider.languageModel(selectedModel)
    const repository = await mkdtemp(join(tmpdir(), "claude-agent-provider-"))
    await mkdir(join(repository, ".git"))
    const options = {
      prompt: [{ role: "system", content: "Review systems." }],
      headers: {
        "x-opencode-agent": "reviewer",
        "x-opencode-directory": repository,
      },
      providerOptions: { "claude-agent": { effort: "xhigh" } },
    }

    const first = await model.doStream(options as never)
    await first.stream.pipeTo(new WritableStream())
    const second = await model.doStream(options as never)
    await second.stream.pipeTo(new WritableStream())

    expect(createClaudeCode).toHaveBeenCalledTimes(2)
    expect(languageModel).toHaveBeenCalledTimes(2)
    for (const [modelId, settings] of languageModel.mock.calls) {
      expect(modelId).toBe(selectedModel)
      expect(settings.effort).toBe("xhigh")
      expect(settings.settings.switchModelsOnFlag).toBe(false)
      expect(settings.fallbackModel).toBeUndefined()
      expect(settings.sdkOptions?.onUserDialog).toBeUndefined()
      expect(settings.sdkOptions?.supportedDialogKinds).toBeUndefined()
    }
    expect(createClaudeCode.mock.calls[0]?.[0]).toBeUndefined()
  })

  test.each(["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"])("generates with selected ID %s and fixed, isolated SDK settings", async (selectedModel) => {
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
    const model = provider.languageModel(selectedModel)

    await model.doGenerate({
      prompt: [
        { role: "system", content: "Review systems." },
        { role: "user", content: [{ type: "text", text: "Inspect this." }] },
      ],
      headers: {
        "x-opencode-agent": "reviewer",
        "x-opencode-directory": repository,
      },
      providerOptions: { "claude-agent": { effort: "xhigh" } },
    } as never)

    const [modelId, settings] = languageModel.mock.calls[0] as unknown as [
      string,
      Record<string, any>,
    ]
    expect(modelId).toBe(selectedModel)
    expect(settings).toMatchObject({
      cwd: repository,
      effort: "xhigh",
      streamingInput: "always",
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
        switchModelsOnFlag: false,
        autoMemoryEnabled: false,
        disableClaudeAiConnectors: true,
        enableAllProjectMcpServers: false,
      },
    })
    expect(settings.fallbackModel).toBeUndefined()
    expect(settings.sdkOptions?.onUserDialog).toBeUndefined()
    expect(settings.sdkOptions?.supportedDialogKinds).toBeUndefined()
    expect(settings.disallowedTools).toContain("WebFetch")
    expect(settings.systemPrompt).toContain("read-only reviewer using the Claude Agent SDK")
    expect(settings.systemPrompt).toContain("application instructions")
    expect(settings.systemPrompt).not.toContain("Review systems.")
    expect(settings.title).toBe("OpenCode read-only review")
    expect(settings.env.ANTHROPIC_CANARY_TEST).toBeUndefined()
    expect(settings.env.CLAUDE_AGENT_SDK_CLIENT_APP).toBe(
      "opencode-claude-agent/0.1.0",
    )
    expect(doGenerate.mock.calls[0]?.[0].prompt).toEqual([
      { role: "user", content: [{ type: "text", text: "Application instructions (original OpenCode system message, supplied as task context):\n\nReview systems." }] },
      { role: "user", content: [{ type: "text", text: "Inspect this." }] },
    ])
    expect(doGenerate.mock.calls[0]?.[0].headers).toBeUndefined()
    expect(doGenerate.mock.calls[0]?.[0].providerOptions).toBeUndefined()
  })

  test("bounds history including the application-context envelope", async () => {
    const { createOpenCodeClaudeAgent } = await import("../src/provider.js")
    const model = createOpenCodeClaudeAgent({
      apiKey: "claude-agent-cli-authenticated", authenticated: true,
    }).languageModel("claude-fable-5-2")
    const repository = await mkdtemp(join(tmpdir(), "claude-agent-size-"))
    await mkdir(join(repository, ".git"))
    const overhead = Buffer.byteLength(JSON.stringify([{ role: "system", content: "" }]))
    await expect(model.doGenerate({
      prompt: [{ role: "system", content: "x".repeat(4 * 1024 * 1024 - overhead - 20) }],
      headers: { "x-opencode-agent": "reviewer", "x-opencode-directory": repository },
    } as never)).rejects.toThrow("history exceeds 4 MiB")
    expect(languageModel).not.toHaveBeenCalled()
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
    const model = provider.languageModel("claude-fable-5-2")
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
