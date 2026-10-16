import { afterEach, beforeEach, describe, expect, test, vi } from "vitest"

const { execute } = vi.hoisted(() => ({ execute: vi.fn() }))
vi.mock("node:child_process", () => ({
  execFile: Object.assign(vi.fn(), {
    [Symbol.for("nodejs.util.promisify.custom")]: execute,
  }),
}))

describe("v2 existing subscription login", () => {
  beforeEach(() => {
    execute.mockReset()
    vi.stubEnv("OPENCODE_CLAUDE_CLI", "/nix/store/test-claude/bin/claude")
  })
  afterEach(() => vi.unstubAllEnvs())

  test("accepts only a subscription login and sanitizes the child environment", async () => {
    vi.stubEnv("ANTHROPIC_API_KEY", "secret-canary")
    execute.mockResolvedValue({ stdout: '{"loggedIn":true,"authMethod":"claude.ai","email":"private@example.com"}' })
    const { checkClaudeLogin } = await import("../src/auth-v2.js")

    await expect(checkClaudeLogin()).resolves.toEqual({ available: true })
    expect(execute).toHaveBeenCalledWith("/nix/store/test-claude/bin/claude", ["auth", "status"], {
      env: expect.objectContaining({ ANTHROPIC_API_KEY: undefined }),
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    })
  })

  test.each(["", "/usr/bin/claude"])("does not execute an unpinned CLI: %s", async path => {
    vi.stubEnv("OPENCODE_CLAUDE_CLI", path)
    const { checkClaudeLogin } = await import("../src/auth-v2.js")
    await expect(checkClaudeLogin()).resolves.toEqual({
      available: false,
      reason: "OPENCODE_CLAUDE_CLI must name the Nix-provided Claude executable",
    })
    expect(execute).not.toHaveBeenCalled()
  })

  test.each([
    '{"loggedIn":false,"email":"private@example.com"}',
    '{"loggedIn":true,"authMethod":"api-key","email":"private@example.com"}',
    "private@example.com: invalid JSON",
    "null",
  ])("rejects unusable status without leaking its contents: %s", async stdout => {
    execute.mockResolvedValue({ stdout })
    const { checkClaudeLogin } = await import("../src/auth-v2.js")
    const result = await checkClaudeLogin()
    expect(result.available).toBe(false)
    expect(JSON.stringify(result)).not.toContain("private@example.com")
  })

  test("converts process errors and private output to a fixed diagnostic", async () => {
    execute.mockRejectedValue(Object.assign(new Error("private@example.com"), {
      stdout: "account-secret", stderr: "organization-secret",
    }))
    const { checkClaudeLogin } = await import("../src/auth-v2.js")
    await expect(checkClaudeLogin()).resolves.toEqual({
      available: false,
      reason: "The Nix-provided Claude executable did not report a login status",
    })
  })
})
