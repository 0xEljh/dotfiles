import { afterEach, describe, expect, test, vi } from "vitest"

import plugin from "../src/plugin.js"

const SENTINEL = "claude-agent-cli-authenticated"

describe("Fable reviewer routing", () => {
  const expectedModel = "claude-agent/claude-fable-5-1"
  const reviewers = ["reviewer-systems-fable", "reviewer-max"]

  test("registers the exact Fable 5.1 model", async () => {
    const hooks = await plugin({ directory: "/repo" } as never)
    const config: Record<string, any> = {}
    await hooks.config!(config)

    expect(config.provider["claude-agent"].models["claude-fable-5-1"]).toMatchObject({
      name: "Claude Fable 5.1 (Agent SDK)",
      variants: { high: { effort: "high" }, xhigh: { effort: "xhigh" } },
    })
  })

  test.each(reviewers)("passes request identity for %s on Fable 5.1", async (agent) => {
    const hooks = await plugin({ directory: "/repo" } as never)
    const output = { headers: {} }
    await hooks["chat.headers"]!({
      agent,
      sessionID: "review-session",
      model: { providerID: "claude-agent", id: "claude-fable-5-1" },
    } as never, output)

    expect(output.headers).toEqual({
      "x-opencode-agent": agent,
      "x-opencode-directory": "/repo",
      "x-opencode-session": "review-session",
    })
  })

  for (const agent of reviewers) {
    test.each([
      { providerID: "openai", id: "gpt-5.5" },
      { providerID: "anthropic", id: "claude-fable-5-1" },
      { providerID: "claude-agent", id: "claude-fable-5" },
    ])(`rejects a different provider or model for ${agent}: $providerID/$id`, async (model) => {
      const hooks = await plugin({ directory: "/repo" } as never)
      const output = { headers: {} }

      await expect(hooks["chat.headers"]!({
        agent,
        sessionID: "review-session",
        model,
      } as never, output)).rejects.toThrow(
        `Model policy violation: ${agent} requires ${expectedModel}; selected ${model.providerID}/${model.id}`,
      )
      expect(output.headers).toEqual({})
    })
  }

  test.each(["build", "compaction", "title"])("allows other models for %s", async (agent) => {
    const hooks = await plugin({ directory: "/repo" } as never)
    const output = { headers: {} }
    await hooks["chat.headers"]!({
      agent,
      sessionID: "other-session",
      model: { providerID: "openai", id: "gpt-5.5" },
    } as never, output)

    expect(output.headers).toEqual({})
  })
})

describe("OpenCode auth plugin", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  // OpenCode does not catch exceptions from a plugin auth loader: a throw here
  // propagates through Provider.list and turns /config/providers into a 500,
  // which stops the whole TUI from starting. An unusable Claude CLI must cost
  // only this provider.
  test("reports itself unavailable instead of throwing without the Nix CLI path", async () => {
    vi.stubEnv("OPENCODE_CLAUDE_CLI", "")
    const hooks = await plugin({ directory: "/repo" } as never)

    const result = await (hooks.auth as never as {
      loader: (getAuth: () => Promise<unknown>) => Promise<Record<string, unknown>>
    }).loader(async () => ({ type: "api", key: SENTINEL }))

    expect(result.authenticated).toBe(false)
    expect(result.unavailableReason).toContain("OPENCODE_CLAUDE_CLI")
  })

  test("uses a no-input callback instead of the API-key prompt", async () => {
    const hooks = await plugin({ directory: "/repo" } as never)
    const method = hooks.auth?.methods[0]

    expect(method?.type).toBe("oauth")
    expect(method?.prompts).toBeUndefined()
    if (method?.type !== "oauth") throw new Error("Expected OAuth callback method")

    const authorization = await method.authorize()
    expect(authorization).toMatchObject({
      url: "",
      method: "auto",
    })
    expect(authorization.callback).toBeTypeOf("function")
  })
})
