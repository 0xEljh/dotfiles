import { execFile } from "node:child_process"
import { promisify } from "node:util"

import type { Plugin } from "@opencode-ai/plugin"

import {
  AUTH_SENTINEL,
  sanitizeClaudeEnvironment,
} from "./policy.js"

const execFileAsync = promisify(execFile)
const PROVIDER_ID = "claude-agent"

type Availability = { available: true } | { available: false; reason: string }

// Every reason is a fixed string. `claude auth status` reports the account
// email and organization ID, so no part of its output or of an exec failure
// may reach a message OpenCode can display or log.
const MISSING_CLI = "OPENCODE_CLAUDE_CLI must name the Nix-provided Claude executable"
const MISSING_LOGIN = "Run `claude auth login` before using Claude Agent"
const UNREADABLE_STATUS = "The Nix-provided Claude executable did not report a login status"
const MISSING_SENTINEL = "Run `opencode auth login --provider claude-agent`"

async function claudeSubscriptionLogin(): Promise<Availability> {
  const executable = process.env.OPENCODE_CLAUDE_CLI
  if (!executable?.startsWith("/nix/store/")) {
    return { available: false, reason: MISSING_CLI }
  }

  let stdout: string
  try {
    ;({ stdout } = await execFileAsync(executable, ["auth", "status"], {
      env: sanitizeClaudeEnvironment(process.env),
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    }))
  } catch {
    return { available: false, reason: UNREADABLE_STATUS }
  }

  let status: { loggedIn?: boolean; authMethod?: string }
  try {
    status = JSON.parse(stdout)
  } catch {
    return { available: false, reason: UNREADABLE_STATUS }
  }

  if (status.loggedIn === true && status.authMethod === "claude.ai") {
    return { available: true }
  }
  return { available: false, reason: MISSING_LOGIN }
}

const plugin: Plugin = async ({ directory }) => ({
  config: async (config) => {
    config.provider ??= {}
    const providers = config.provider as Record<string, unknown>
    providers[PROVIDER_ID] = {
      name: "Claude Agent",
      npm: new URL("./provider.js", import.meta.url).href,
      models: {
        fable: {
          name: "Claude Fable 5 (Agent SDK)",
          reasoning: true,
          tool_call: false,
          limit: {
            context: 1_000_000,
            output: 128_000,
          },
          variants: {
            high: { effort: "high" },
            xhigh: { effort: "xhigh" },
          },
        },
      },
    } as unknown
  },
  "chat.headers": async (input, output) => {
    if (input.model.providerID !== PROVIDER_ID) return
    output.headers["x-opencode-agent"] = input.agent
    output.headers["x-opencode-directory"] = directory
    output.headers["x-opencode-session"] = input.sessionID
  },
  auth: {
    provider: PROVIDER_ID,
    methods: [
      {
        type: "oauth",
        label: "Use existing Claude login",
        async authorize() {
          return {
            url: "",
            instructions: "Verifying the existing Claude login",
            method: "auto",
            async callback() {
              const login = await claudeSubscriptionLogin()
              if (!login.available) return { type: "failed" }
              return { type: "success", key: AUTH_SENTINEL }
            },
          }
        },
      },
    ],
    // OpenCode does not catch exceptions raised here: a throw propagates out
    // of Provider.list and makes /config/providers answer 500, which stops the
    // whole TUI from starting. Report unavailability instead, so a missing or
    // logged-out Claude CLI costs only this provider. The reason travels to the
    // provider factory, which still fails closed when the model is used.
    async loader(getAuth) {
      const auth = await getAuth()
      if (auth.type !== "api" || auth.key !== AUTH_SENTINEL) {
        return { authenticated: false, unavailableReason: MISSING_SENTINEL }
      }
      const login = await claudeSubscriptionLogin()
      if (!login.available) {
        return { authenticated: false, unavailableReason: login.reason }
      }
      return { authenticated: true }
    },
  },
})

export default plugin
