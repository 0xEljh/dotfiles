import { describe, expect, test } from "vitest"

describe("v2 dynamic AI SDK factory", () => {
  test("exposes one create factory and rejects absent/wrong sentinels", async () => {
    const module = await import("../src/provider-v2.js")
    expect(Object.keys(module).filter(name => name.startsWith("create"))).toEqual(["createClaudeAgentV2"])
    expect(() => module.createClaudeAgentV2()).toThrow("authentication sentinel")
    expect(() => module.createClaudeAgentV2({ apiKey: "wrong" })).toThrow("authentication sentinel")
  })

  test.each(["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])("constructs the existing restricted V3 adapter for %s without querying Claude", async modelID => {
    const { createClaudeAgentV2 } = await import("../src/provider-v2.js")
    const provider = createClaudeAgentV2({ name: "claude-agent", apiKey: "claude-agent-cli-authenticated" })
    expect(provider.languageModel(modelID)).toMatchObject({
      specificationVersion: "v3", provider: "claude-agent", modelId: modelID,
    })
    expect(() => provider.languageModel("unsupported")).toThrow("Unsupported Claude Agent model")
  })
})
