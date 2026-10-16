import { afterEach, beforeEach, describe, expect, test, vi } from "vitest"

const { checkClaudeLogin } = vi.hoisted(() => ({ checkClaudeLogin: vi.fn() }))
vi.mock("../src/auth-v2.js", () => ({ checkClaudeLogin }))

describe("v2 integration command output", () => {
  let previousExitCode: typeof process.exitCode
  beforeEach(() => {
    vi.resetModules()
    checkClaudeLogin.mockReset()
    previousExitCode = process.exitCode
    vi.spyOn(process.stdout, "write").mockReturnValue(true)
    vi.spyOn(process.stderr, "write").mockReturnValue(true)
  })
  afterEach(() => {
    process.exitCode = previousExitCode
    vi.restoreAllMocks()
  })

  test("writes exactly the sentinel and newline on success", async () => {
    checkClaudeLogin.mockResolvedValue({ available: true })
    await import("../src/connect-v2.js")
    expect(process.stdout.write).toHaveBeenCalledExactlyOnceWith("claude-agent-cli-authenticated\n")
    expect(process.stderr.write).not.toHaveBeenCalled()
    expect(process.exitCode ?? 0).toBe(0)
  })

  test("writes only the fixed failure reason to stderr and exits nonzero", async () => {
    checkClaudeLogin.mockResolvedValue({ available: false, reason: "Run `claude auth login` before using Claude Agent" })
    await import("../src/connect-v2.js")
    expect(process.stdout.write).not.toHaveBeenCalled()
    expect(process.stderr.write).toHaveBeenCalledExactlyOnceWith("Run `claude auth login` before using Claude Agent\n")
    expect(process.exitCode).toBe(1)
  })

  test("never prints an unexpected exception or returns a successful credential", async () => {
    checkClaudeLogin.mockRejectedValue(new Error("private@example.com organization-secret"))
    await import("../src/connect-v2.js")
    expect(process.stdout.write).not.toHaveBeenCalled()
    expect(process.stderr.write).toHaveBeenCalledExactlyOnceWith("Claude Agent login verification failed\n")
    expect(process.exitCode).toBe(1)
  })
})
