import { execFile } from "node:child_process"
import { promisify } from "node:util"

import { sanitizeClaudeEnvironment } from "./policy.js"

export type Availability = { available: true } | { available: false; reason: string }

const execFileAsync = promisify(execFile)
const UNREADABLE_STATUS = "The Nix-provided Claude executable did not report a login status"

// Status output and process errors can contain account information. Only fixed
// diagnostics may leave this helper, including through the integration command.
export async function checkClaudeLogin(): Promise<Availability> {
  const executable = process.env.OPENCODE_CLAUDE_CLI
  if (!executable?.startsWith("/nix/store/")) {
    return {
      available: false,
      reason: "OPENCODE_CLAUDE_CLI must name the Nix-provided Claude executable",
    }
  }

  try {
    const { stdout } = await execFileAsync(executable, ["auth", "status"], {
      env: sanitizeClaudeEnvironment(process.env),
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    })
    const status: { loggedIn?: boolean; authMethod?: string } | null = JSON.parse(stdout)
    if (status?.loggedIn === true && status.authMethod === "claude.ai") {
      return { available: true }
    }
    return {
      available: false,
      reason: "Run `claude auth login` before using Claude Agent",
    }
  } catch {
    return { available: false, reason: UNREADABLE_STATUS }
  }
}
