import { afterEach, describe, expect, test, vi } from "vitest"

import plugin from "../src/plugin.js"

const SENTINEL = "claude-agent-cli-authenticated"

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
