import { mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { describe, expect, test } from "vitest"

import {
  assertAllowedAgent,
  assertSafeGrepPath,
  assertSafeGlobPattern,
  assertSafeToolPath,
  resolveWorkspace,
  sanitizeClaudeEnvironment,
  globLiteralPrefix,
  splitSystemPrompt,
} from "../src/policy.js"

describe("sanitizeClaudeEnvironment", () => {
  test("keeps OS basics and removes credential and cloud prefixes", () => {
    const env = sanitizeClaudeEnvironment({
      HOME: "/home/test",
      PATH: "/bin",
      LANG: "en_US.UTF-8",
      ANTHROPIC_API_KEY: "secret",
      ANTHROPIC_CANARY_random: "secret",
      CLAUDE_CANARY_random: "secret",
      AWS_CANARY_random: "secret",
      GOOGLE_CANARY_random: "secret",
      GCLOUD_PROJECT: "secret",
      CLOUD_ML_REGION: "secret",
      UNRELATED_SECRET: "secret",
    })

    expect(env).toMatchObject({
      HOME: "/home/test",
      PATH: "/bin",
      LANG: "en_US.UTF-8",
      CLAUDE_AGENT_SDK_CLIENT_APP: "opencode-claude-agent/0.1.0",
    })
    for (const key of [
      "ANTHROPIC_API_KEY",
      "ANTHROPIC_CANARY_random",
      "CLAUDE_CANARY_random",
      "AWS_CANARY_random",
      "GOOGLE_CANARY_random",
      "GCLOUD_PROJECT",
      "CLOUD_ML_REGION",
      "UNRELATED_SECRET",
    ]) {
      expect(env).toHaveProperty(key, undefined)
    }
  })
})

describe("workspace policy", () => {
  test("accepts a repository and rejects HOME", async () => {
    const home = await mkdtemp(join(tmpdir(), "claude-agent-home-"))
    const repository = join(home, "repo")
    await mkdir(join(repository, ".git"), { recursive: true })

    await expect(resolveWorkspace(repository, home)).resolves.toBe(repository)
    await expect(resolveWorkspace(home, home)).rejects.toThrow("home directory")
  })

  test("rejects parent traversal, protected files, and symlinks", async () => {
    const root = await mkdtemp(join(tmpdir(), "claude-agent-repo-"))
    await mkdir(join(root, ".git"))
    await writeFile(join(root, "safe.txt"), "safe")
    await writeFile(join(root, ".env"), "secret")
    await mkdir(join(root, ".config", "gh"), { recursive: true })
    await writeFile(join(root, ".config", "gh", "hosts.yml"), "secret")
    await symlink("safe.txt", join(root, "linked.txt"))

    await expect(assertSafeToolPath(root, "../outside")).rejects.toThrow(
      "parent traversal",
    )
    await expect(assertSafeToolPath(root, ".env")).rejects.toThrow(
      "protected path",
    )
    await expect(
      assertSafeToolPath(root, ".config/gh/hosts.yml"),
    ).rejects.toThrow("protected path")
    await expect(assertSafeToolPath(root, "linked.txt")).rejects.toThrow(
      "symbolic link",
    )
    await expect(assertSafeToolPath(root, "safe.txt")).resolves.toBe(
      join(root, "safe.txt"),
    )
    await expect(assertSafeGrepPath(root, ".")).rejects.toThrow("one safe file")
    await expect(assertSafeGrepPath(root, "safe.txt")).resolves.toBe(
      join(root, "safe.txt"),
    )
  })

  test("rejects absolute and parent-traversing glob patterns", () => {
    expect(() => assertSafeGlobPattern("src/**/*.ts")).not.toThrow()
    expect(() => assertSafeGlobPattern("../outside/**")).toThrow("glob pattern")
    expect(() => assertSafeGlobPattern("/etc/**")).toThrow("glob pattern")
    expect(globLiteralPrefix("src/lib/**/*.ts")).toBe(join("src", "lib"))
    expect(globLiteralPrefix("**/*.ts")).toBe(".")
  })
})

describe("request policy", () => {
  test("accepts only the additive systems agent", () => {
    expect(() => assertAllowedAgent("reviewer-systems-fable")).not.toThrow()
    expect(() => assertAllowedAgent("build")).toThrow("not allowed")
  })

  test("extracts system text exactly once", () => {
    const result = splitSystemPrompt([
      { role: "system", content: "System one" },
      { role: "system", content: "System two" },
      { role: "user", content: [{ type: "text", text: "Question" }] },
    ])

    expect(result.systemPrompt).toBe("System one\n\nSystem two")
    expect(result.prompt).toEqual([
      { role: "user", content: [{ type: "text", text: "Question" }] },
    ])
  })
})
