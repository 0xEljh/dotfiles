import { fileURLToPath } from "node:url"
import { beforeEach, describe, expect, test, vi } from "vitest"
import type { Plugin } from "@opencode/plugin"
import type { IntegrationEditor } from "@opencode/plugin/promise/integration"
import type { ProviderEditor } from "@opencode/plugin/promise/provider"
import type { ModelHookOptions } from "@opencode/plugin/promise/registration"
import type { SessionContext, SessionHooks, SessionModelRequest } from "@opencode/plugin/promise/session"

const { checkClaudeLogin } = vi.hoisted(() => ({ checkClaudeLogin: vi.fn() }))
vi.mock("../src/auth-v2.js", () => ({ checkClaudeLogin }))

const sentinel = "claude-agent-cli-authenticated"
const registration = { dispose: async () => {} }

async function setup() {
  const { default: plugin } = await import("../src/plugin-v2.js")
  const { Agent, Model, Provider } = await import("@opencode/plugin")
  let definition: Parameters<ProviderEditor["add"]>[0] | undefined
  const hooks = new Map<keyof SessionHooks, {
    callback: (input: never) => Promise<void> | void
    options?: ModelHookOptions
  }>()
  const add = vi.fn<ProviderEditor["add"]>(value => { definition = value })
  const method = vi.fn<IntegrationEditor["method"]["update"]>()
  const update = vi.fn<IntegrationEditor["update"]>((_id, change) => change({ id: "claude-agent", name: "claude-agent" }))
  const unused = () => { throw new Error("Unexpected editor operation") }
  const providerEditor: ProviderEditor = {
    add, list: unused, get: unused, update: unused, remove: unused,
    models: { set: unused, update: unused, remove: unused },
  }
  const integrationEditor: IntegrationEditor = {
    update, list: unused, get: unused, remove: unused,
    method: { update: method, list: unused, remove: unused },
  }
  const active = vi.fn().mockResolvedValue({ type: "credential", id: "credential", label: "Claude", method: "key" })
  const resolve = vi.fn().mockResolvedValue({ type: "key", key: sentinel })
  const status = vi.fn().mockResolvedValue(undefined)
  const get = vi.fn().mockResolvedValue({ agent: "build", location: { directory: "/session/repository" } })
  const hook = vi.fn(async (name: keyof SessionHooks, callback: (input: never) => Promise<void> | void, options?: ModelHookOptions) => {
    hooks.set(name, { callback, options })
    return registration
  })
  await plugin.setup({
    location: { directory: "/wrong/plugin-instance" },
    provider: { transform: async (callback: (editor: ProviderEditor) => void) => { callback(providerEditor); return registration } },
    integration: {
      transform: async (callback: (editor: IntegrationEditor) => void) => { callback(integrationEditor); return registration },
      connection: { active, resolve, status },
    },
    session: { hook, get },
  } as unknown as Plugin.Context)
  const request = (overrides: Partial<SessionModelRequest> = {}): SessionModelRequest => ({
    sessionID: "session-one" as SessionModelRequest["sessionID"],
    agent: Agent.ID.make("reviewer"),
    kind: "primary",
    model: { providerID: Provider.ID.make("claude-agent"), id: Model.ID.make("claude-fable-5-2") },
    headers: { "existing-header": "retained" },
    ...overrides,
  })
  const dispatch = async <Name extends "context" | "model.request">(name: Name, input: SessionHooks[Name]) => {
    const registered = hooks.get(name)
    if (!registered || (registered.options?.providerID && registered.options.providerID !== input.model.providerID)) return
    await registered.callback(input as never)
  }
  const context = (overrides: Partial<SessionContext> = {}): SessionContext => ({
    sessionID: "session-one" as SessionContext["sessionID"],
    agent: Agent.ID.make("reviewer"),
    model: request().model,
    system: [{ type: "text", text: "Review this proposal." }],
    messages: [],
    options: { temperature: 0.2 },
    tools: Object.fromEntries(["read", "question", "execute", "mcp_lookup"].map(name => [
      name, { description: name, input: { type: "object" } },
    ])),
    ...overrides,
  })
  return {
    definition: definition!, add, method, update, active, resolve, status, get, hook, request, context,
    run: (input = request()) => dispatch("model.request", input),
    runContext: (input = context()) => dispatch("context", input),
    Agent, Model, Provider,
  }
}

describe("v2 reviewer registration and routing", () => {
  beforeEach(() => checkClaudeLogin.mockReset().mockResolvedValue({ available: true }))

  test("registers published-schema models, auto integration, and an absolute packaged command without checking auth at setup", async () => {
    const h = await setup()
    const { Schema } = await import("effect")
    expect(Schema.is(h.Provider.Info)(h.definition.info)).toBe(true)
    expect(h.definition.info).toMatchObject({
      id: "claude-agent", integrationID: "claude-agent", activation: "auto",
      package: `aisdk:${new URL("../src/provider-v2.js", import.meta.url).href}`,
    })
    expect(h.definition.models.map(model => model.id)).toEqual(["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"])
    for (const model of h.definition.models) {
      expect(Schema.is(h.Model.Info)(model)).toBe(true)
      expect(model).toMatchObject({
        capabilities: { tools: false, input: ["text"], output: ["text"] },
        limit: { context: 1_000_000, output: 128_000 },
        variants: [{ id: "high", settings: { effort: "high" } }, { id: "xhigh", settings: { effort: "xhigh" } }],
      })
    }
    expect(h.method).toHaveBeenCalledWith({
      integrationID: "claude-agent",
      method: {
        id: "existing-login", type: "command", label: "Use existing Claude login",
        command: [fileURLToPath(new URL("../src/connect-v2", import.meta.url))],
      },
    })
    expect(h.hook.mock.calls.find(([name]) => name === "model.request")).toHaveLength(2) // Wrong-provider requests must reach policy.
    expect(h.active).not.toHaveBeenCalled()
    expect(checkClaudeLogin).not.toHaveBeenCalled()
  })

  test("removes OpenCode tools from Claude Agent calls without changing the rest of the context", async () => {
    const h = await setup()
    const input = h.context()
    const original = structuredClone(input)
    const tools = input.tools
    await h.runContext(input)
    expect(input).toEqual({ ...original, tools: {} })
    expect(tools).toEqual(original.tools)
    await h.runContext(input)
    expect(input.tools).toEqual({})
  })

  test.each(["openai", "anthropic"])("preserves OpenCode tools for %s calls", async provider => {
    const h = await setup()
    const input = h.context({ model: { providerID: h.Provider.ID.make(provider), id: h.Model.ID.make("other-model") } })
    const original = structuredClone(input)
    await h.runContext(input)
    expect(input).toEqual(original)
  })

  test("uses each request's agent/session location, preserves headers, and observes a moved session", async () => {
    const h = await setup()
    const first = h.request()
    await h.run(first)
    expect(h.get).toHaveBeenCalledWith({ sessionID: "session-one" })
    expect(first.headers).toEqual({
      "existing-header": "retained", "x-opencode-agent": "reviewer",
      "x-opencode-directory": "/session/repository", "x-opencode-session": "session-one",
    })
    h.get.mockResolvedValue({ agent: "build", location: { directory: "/moved/repository" } })
    const second = h.request({ agent: h.Agent.ID.make("reviewer-systems-fable") })
    await h.run(second)
    expect(second.headers["x-opencode-directory"]).toBe("/moved/repository")
    expect(second.headers["x-opencode-agent"]).toBe("reviewer-systems-fable")
    expect(h.active).toHaveBeenCalledTimes(2)
    expect(checkClaudeLogin).toHaveBeenCalledTimes(2)
    expect(h.status).toHaveBeenLastCalledWith(expect.objectContaining({ status: undefined }))
  })

  test.each(["primary", "generate"] as const)("rejects wrong provider/model for a %s reviewer request before attaching identity", async kind => {
    const h = await setup()
    for (const model of [
      { providerID: h.Provider.ID.make("openai"), id: h.Model.ID.make("gpt-5.5") },
      { providerID: h.Provider.ID.make("claude-agent"), id: h.Model.ID.make("unsupported") },
    ]) {
      const input = h.request({ kind, model })
      await expect(h.run(input)).rejects.toThrow("Model policy violation")
      expect(input.headers).toEqual({ "existing-header": "retained" })
    }
    expect(h.active).not.toHaveBeenCalled()
  })

  test.each(["title", "compaction"] as const)("allows the configured small model for %s even with reviewer identity", async kind => {
    const h = await setup()
    const input = h.request({ kind, model: { providerID: h.Provider.ID.make("openai"), id: h.Model.ID.make("small-model") } })
    await h.run(input)
    expect(input.headers).toEqual({ "existing-header": "retained" })
    expect(h.active).not.toHaveBeenCalled()
  })

  test("rejects a non-reviewer selecting Claude and leaves unrelated requests alone", async () => {
    const h = await setup()
    for (const agent of ["build", "reviewer-independent", "reviewer-architecture", "implementation-reviewer", "implementation-reviewer-heavy", "acceptance-redteam"]) {
      await expect(h.run(h.request({ agent: h.Agent.ID.make(agent) }))).rejects.toThrow("not allowed")
    }
    await h.run(h.request({ agent: h.Agent.ID.make("build"), model: { providerID: h.Provider.ID.make("openai"), id: h.Model.ID.make("gpt-5.5") } }))
    expect(h.active).not.toHaveBeenCalled()
  })

  test.each([undefined, { type: "key", key: "wrong" }, { type: "external", methodID: "existing-login" }])("fails closed for an unusable credential: %j", async credential => {
    const h = await setup()
    h.resolve.mockResolvedValue(credential)
    const input = h.request()
    await expect(h.run(input)).rejects.toThrow("Connect the Claude Agent integration")
    expect(h.status).toHaveBeenCalledWith(expect.objectContaining({ status: { status: "needs_auth", message: "Connect the Claude Agent integration" } }))
    expect(input.headers).toEqual({ "existing-header": "retained" })
    expect(checkClaudeLogin).not.toHaveBeenCalled()
  })

  test("missing connection and expired subscription cost only the selected request", async () => {
    const h = await setup()
    h.active.mockResolvedValueOnce(undefined)
    await expect(h.run()).rejects.toThrow("Connect the Claude Agent integration")
    expect(h.resolve).not.toHaveBeenCalled()
    checkClaudeLogin.mockResolvedValueOnce({ available: false, reason: "Run `claude auth login` before using Claude Agent" })
    await expect(h.run()).rejects.toThrow("Run `claude auth login`")
    expect(h.status).toHaveBeenLastCalledWith(expect.objectContaining({ status: { status: "needs_auth", message: "Run `claude auth login` before using Claude Agent" } }))
    await expect(h.run()).resolves.toBeUndefined()
    expect(h.status).toHaveBeenLastCalledWith(expect.objectContaining({ status: undefined }))
  })

  test.each(["active", "resolve", "status", "get"] as const)("fails closed and hides raw %s API errors", async api => {
    const h = await setup()
    h[api].mockRejectedValueOnce(new Error("private@example.com credential-secret"))
    const input = h.request()
    const messages = {
      active: "Claude Agent could not resolve its connection",
      resolve: "Claude Agent could not resolve its credential",
      status: "Claude Agent could not report its login status",
      get: "Claude Agent could not resolve the request workspace",
    }
    await expect(h.run(input)).rejects.toMatchObject({ message: messages[api] })
    expect(input.headers).toEqual({ "existing-header": "retained" })
  })
})
