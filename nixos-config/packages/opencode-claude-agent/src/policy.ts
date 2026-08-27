import { lstat, realpath, stat } from "node:fs/promises"
import { dirname, isAbsolute, join, parse, relative, resolve, sep } from "node:path"

export const AGENT_NAME = "reviewer-systems-fable"
export const AUTH_SENTINEL = "claude-agent-cli-authenticated"
export const CLIENT_APP = "opencode-claude-agent/0.1.0"

const ENV_ALLOWLIST = new Set([
  "HOME",
  "LANG",
  "LC_ALL",
  "LOGNAME",
  "PATH",
  "SHELL",
  "TERM",
  "TMPDIR",
  "USER",
])

const PROTECTED_COMPONENTS = new Set([
  ".aws",
  ".claude",
  ".gnupg",
  ".kube",
  ".ssh",
])

const PROTECTED_NAMES = new Set([
  ".git-credentials",
  ".netrc",
  ".npmrc",
  ".pypirc",
  "credentials",
])

type PromptMessage = {
  role: string
  content: unknown
}

export function sanitizeClaudeEnvironment(
  source: NodeJS.ProcessEnv,
): Record<string, string | undefined> {
  const result: Record<string, string | undefined> = {}
  for (const [key, value] of Object.entries(source)) {
    result[key] = ENV_ALLOWLIST.has(key) ? value : undefined
  }
  result.CLAUDE_AGENT_SDK_CLIENT_APP = CLIENT_APP
  return result
}

export function assertAllowedAgent(agent: string | undefined): void {
  if (agent !== AGENT_NAME) {
    throw new Error(`Agent ${agent ?? "<missing>"} is not allowed to use Fable`)
  }
}

export async function resolveWorkspace(
  directory: string,
  home = process.env.HOME,
): Promise<string> {
  if (!directory || !isAbsolute(directory)) {
    throw new Error("Claude Agent requires an absolute workspace directory")
  }

  const canonicalHome = home ? await realpath(home) : undefined
  let current = await realpath(directory)
  const filesystemRoot = parse(current).root

  if (current === filesystemRoot) {
    throw new Error("Claude Agent cannot use a filesystem root as its workspace")
  }
  if (canonicalHome && current === canonicalHome) {
    throw new Error("Claude Agent cannot use the home directory as its workspace")
  }

  while (current !== filesystemRoot && current !== canonicalHome) {
    try {
      await stat(join(current, ".git"))
      return current
    } catch {
      current = dirname(current)
    }
  }

  throw new Error("Claude Agent requires a repository workspace")
}

function hasParentTraversal(path: string): boolean {
  return path.split(/[\\/]+/).some((component) => component === "..")
}

export function assertSafeGlobPattern(pattern: string): void {
  if (!pattern || isAbsolute(pattern) || hasParentTraversal(pattern)) {
    throw new Error("Claude Agent rejected unsafe glob pattern")
  }
}

export function globLiteralPrefix(pattern: string): string {
  assertSafeGlobPattern(pattern)
  const components: string[] = []
  for (const component of pattern.split(/[\\/]+/)) {
    if (/[*?[{]/.test(component)) break
    components.push(component)
  }
  return components.join(sep) || "."
}

function isProtectedPath(relativePath: string): boolean {
  const components = relativePath.toLowerCase().split(sep)
  const name = components.at(-1) ?? ""

  if (name === ".env" || name.startsWith(".env.")) return true
  if (PROTECTED_NAMES.has(name)) return true
  if (name.startsWith("id_") || name.endsWith(".pem") || name.endsWith(".key")) {
    return true
  }
  if (components.some((component) => PROTECTED_COMPONENTS.has(component))) {
    return true
  }
  return components.some(
    (component, index) => component === ".config" && components[index + 1] === "gh",
  )
}

async function assertNoSymlinks(root: string, target: string): Promise<void> {
  const targetRelative = relative(root, target)
  let current = root
  for (const component of targetRelative.split(sep).filter(Boolean)) {
    current = join(current, component)
    const info = await lstat(current)
    if (info.isSymbolicLink()) {
      throw new Error(`Claude Agent rejected symbolic link: ${current}`)
    }
  }
}

export async function assertSafeToolPath(
  workspace: string,
  requestedPath: string,
): Promise<string> {
  if (hasParentTraversal(requestedPath)) {
    throw new Error("Claude Agent rejected parent traversal")
  }

  const root = await realpath(workspace)
  const candidate = resolve(root, requestedPath)
  const candidateRelative = relative(root, candidate)
  if (
    candidateRelative === ".." ||
    candidateRelative.startsWith(`..${sep}`) ||
    isAbsolute(candidateRelative)
  ) {
    throw new Error("Claude Agent rejected path outside the workspace")
  }
  if (isProtectedPath(candidateRelative)) {
    throw new Error("Claude Agent rejected protected path")
  }

  await assertNoSymlinks(root, candidate)
  const canonical = await realpath(candidate)
  const canonicalRelative = relative(root, canonical)
  if (canonicalRelative.startsWith(`..${sep}`) || isAbsolute(canonicalRelative)) {
    throw new Error("Claude Agent rejected path outside the workspace")
  }
  return canonical
}

export async function assertSafeGrepPath(
  workspace: string,
  requestedPath: string,
): Promise<string> {
  const canonical = await assertSafeToolPath(workspace, requestedPath)
  if (!(await stat(canonical)).isFile()) {
    throw new Error("Claude Agent requires Grep to target one safe file")
  }
  return canonical
}

function systemText(content: unknown): string {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .flatMap((part) => {
      if (typeof part === "string") return [part]
      if (
        part &&
        typeof part === "object" &&
        "type" in part &&
        part.type === "text" &&
        "text" in part &&
        typeof part.text === "string"
      ) {
        return [part.text]
      }
      return []
    })
    .join("\n")
}

export function splitSystemPrompt<T extends PromptMessage>(prompt: T[]): {
  systemPrompt: string
  prompt: T[]
} {
  const system: string[] = []
  const remainder: T[] = []
  for (const message of prompt) {
    if (message.role === "system") {
      const text = systemText(message.content).trim()
      if (text) system.push(text)
      continue
    }
    remainder.push(message)
  }
  return { systemPrompt: system.join("\n\n"), prompt: remainder }
}
