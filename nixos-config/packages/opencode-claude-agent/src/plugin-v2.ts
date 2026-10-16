import { fileURLToPath } from "node:url"

import { Integration, Model, Plugin, Provider } from "@opencode/plugin"

import { checkClaudeLogin } from "./auth-v2.js"
import {
  AUTH_SENTINEL,
  MODELS,
  PROVIDER_ID,
  assertAllowedAgent,
  assertReviewerModel,
} from "./policy.js"

const MISSING_SENTINEL = "Connect the Claude Agent integration"

export default Plugin.define({
  id: "opencode-claude-agent",
  async setup(ctx) {
    const providerID = Provider.ID.make(PROVIDER_ID)
    await ctx.provider.transform(editor => editor.add({
      info: {
        ...Provider.Info.empty(providerID),
        integrationID: Integration.ID.make(PROVIDER_ID),
        name: "Claude Agent",
        package: `aisdk:${new URL("./provider-v2.js", import.meta.url).href}`,
      },
      models: Object.entries(MODELS).map(([id, name]): Model.Info => ({
        ...Model.Info.default(providerID, Model.ID.make(id)),
        name,
        capabilities: { tools: false, input: ["text"], output: ["text"] },
        limit: { context: 1_000_000, output: 128_000 },
        variants: ["high", "xhigh"].map(effort => ({
          id: Model.VariantID.make(effort),
          settings: { effort },
        })),
      })),
    }))
    await ctx.integration.transform(editor => {
      editor.update(PROVIDER_ID, integration => { integration.name = "Claude Agent" })
      editor.method.update({
        integrationID: PROVIDER_ID,
        method: {
          id: "existing-login",
          type: "command",
          label: "Use existing Claude login",
          // Nix installs this launcher beside the compiled helper. Its absolute
          // Node interpreter does not depend on PATH or the OpenCode executable.
          command: [fileURLToPath(new URL("./connect-v2", import.meta.url))],
        },
      })
    })

    // Unscoped so a reviewer selecting a different provider still hits policy.
    await ctx.session.hook("model.request", async request => {
      if (request.kind === "primary" || request.kind === "generate") {
        assertReviewerModel(request.agent, request.model.providerID, request.model.id)
      }
      if (request.model.providerID !== PROVIDER_ID) return
      assertAllowedAgent(request.agent)

      const connection = await ctx.integration.connection.active(PROVIDER_ID).catch(() => {
        throw new Error("Claude Agent could not resolve its connection")
      })
      const credential = connection && await ctx.integration.connection.resolve(connection).catch(() => {
        throw new Error("Claude Agent could not resolve its credential")
      })
      const login = credential?.type === "key" && credential.key === AUTH_SENTINEL
        ? await checkClaudeLogin()
        : { available: false as const, reason: MISSING_SENTINEL }
      if (connection) {
        await ctx.integration.connection.status({
          integrationID: PROVIDER_ID,
          connection,
          status: login.available ? undefined : { status: "needs_auth", message: login.reason },
        }).catch(() => {
          throw new Error("Claude Agent could not report its login status")
        })
      }
      if (!login.available) throw new Error(login.reason)

      const session = await ctx.session.get({ sessionID: request.sessionID }).catch(() => {
        throw new Error("Claude Agent could not resolve the request workspace")
      })
      request.headers["x-opencode-agent"] = request.agent
      request.headers["x-opencode-directory"] = session.location.directory
      request.headers["x-opencode-session"] = request.sessionID
    })
  },
})
