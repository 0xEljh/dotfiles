#!/usr/bin/env node

import { spawn } from "node:child_process"
import { createInterface } from "node:readline"
import { statSync } from "node:fs"
import path from "node:path"

const SERVER_NAME = "opencode-bridge"
const SERVER_VERSION = "0.1.0"
const DEFAULT_TIMEOUT_MS = readIntegerEnv("OPENCODE_BRIDGE_TIMEOUT_MS", 600_000)
const MAX_OUTPUT_CHARS = readIntegerEnv("OPENCODE_BRIDGE_MAX_OUTPUT_CHARS", 120_000)
const MAX_PROMPT_CHARS = readIntegerEnv("OPENCODE_BRIDGE_MAX_PROMPT_CHARS", 100_000)
const OPENCODE_BIN = process.env.OPENCODE_BRIDGE_OPENCODE_BIN || "opencode"
const DEFAULT_MODEL = process.env.OPENCODE_BRIDGE_DEFAULT_MODEL || "openai/gpt-5.5"
const DEFAULT_VARIANT = process.env.OPENCODE_BRIDGE_DEFAULT_VARIANT || "xhigh"

const allowedAgents = readAllowList("OPENCODE_BRIDGE_ALLOWED_AGENTS")
const allowedModels = readAllowList("OPENCODE_BRIDGE_ALLOWED_MODELS")

const tools = [
  {
    name: "opencode_implement_design",
    title: "Implement design with opencode",
    description:
      `Ask opencode to implement a Markdown design doc. Defaults to ${DEFAULT_MODEL} ${DEFAULT_VARIANT}.`,
    inputSchema: {
      type: "object",
      additionalProperties: false,
      properties: {
        design_path: {
          type: "string",
          description: "Path to the .md design doc, relative to CLAUDE_PROJECT_DIR.",
        },
        agent: {
          type: "string",
          description: "Optional opencode primary/all-mode agent name.",
        },
        model: {
          type: "string",
          description: `Optional opencode model. Defaults to ${DEFAULT_MODEL}.`,
        },
      },
      required: ["design_path"],
    },
    _meta: {
      "anthropic/maxResultSizeChars": Math.min(MAX_OUTPUT_CHARS + 4_000, 500_000),
    },
  },
]

const rl = createInterface({ input: process.stdin })

rl.on("line", async (line) => {
  if (!line.trim()) return

  let message
  try {
    message = JSON.parse(line)
  } catch (error) {
    writeError(null, -32700, `Parse error: ${error.message}`)
    return
  }

  try {
    await handleMessage(message)
  } catch (error) {
    if (message.id !== undefined) {
      writeError(message.id, -32603, error.message || String(error))
    }
  }
})

async function handleMessage(message) {
  if (!message.method) return

  switch (message.method) {
    case "initialize":
      writeResult(message.id, {
        protocolVersion: message.params?.protocolVersion || "2025-06-18",
        capabilities: {
          tools: {
            listChanged: false,
          },
        },
        serverInfo: {
          name: SERVER_NAME,
          version: SERVER_VERSION,
        },
      })
      break

    case "notifications/initialized":
    case "notifications/cancelled":
      break

    case "ping":
      writeResult(message.id, {})
      break

    case "tools/list":
      writeResult(message.id, { tools })
      break

    case "tools/call":
      await handleToolCall(message)
      break

    default:
      if (message.id !== undefined) {
        writeError(message.id, -32601, `Method not found: ${message.method}`)
      }
  }
}

async function handleToolCall(message) {
  const name = message.params?.name
  const args = message.params?.arguments || {}

  try {
    const result = await callTool(name, args)
    writeResult(message.id, toolResult(result.text, false, result.structuredContent))
  } catch (error) {
    writeResult(message.id, toolResult(error.message || String(error), true))
  }
}

async function callTool(name, args) {
  switch (name) {
    case "opencode_implement_design":
      return implementDesign(args)
    default:
      throw new Error(`Unknown tool: ${name}`)
  }
}

async function implementDesign(input) {
  const designPath = resolveProjectFile(
    requireString(input.design_path, "design_path"),
    "design_path",
    { markdownOnly: true },
  )
  const projectRoot = getProjectRoot()
  const designPathForPrompt = path.relative(projectRoot, designPath)
  const prompt = `Implement ${designPathForPrompt}`

  return runOpencode({
    prompt,
    agent: input.agent,
    model: input.model,
    directory: ".",
  })
}

async function runOpencode(input) {
  const prompt = requireString(input.prompt, "prompt")
  if (prompt.length > MAX_PROMPT_CHARS) {
    throw new Error(`prompt exceeds ${MAX_PROMPT_CHARS} characters`)
  }

  const agent = optionalString(input.agent, "agent")
  const model = optionalString(input.model, "model") || DEFAULT_MODEL
  const variant = optionalString(input.variant, "variant") || DEFAULT_VARIANT
  const directory = resolveDirectory(optionalString(input.directory, "directory"))
  const timeoutMs = normalizeTimeout(input.timeout_ms)
  const auto = input.auto === true

  validateAllowList(agent, allowedAgents, "agent", "OPENCODE_BRIDGE_ALLOWED_AGENTS")
  validateAllowList(model, allowedModels, "model", "OPENCODE_BRIDGE_ALLOWED_MODELS")

  const args = ["run", "--dir", directory, "--format", "json"]
  if (agent) args.push("--agent", agent)
  if (model) args.push("--model", model)
  if (variant) args.push("--variant", variant)
  if (auto) args.push("--auto")
  args.push(prompt)

  const execution = await runProcess(OPENCODE_BIN, args, directory, timeoutMs)
  const commandSummary = summarizeCommand(OPENCODE_BIN, args)
  const output = formatExecution(commandSummary, execution)

  if (execution.exitCode !== 0) {
    throw new Error(output)
  }

  return {
    text: output,
    structuredContent: {
      ok: true,
      exitCode: execution.exitCode,
      timedOut: execution.timedOut,
      command: commandSummary,
      stdout: execution.stdout,
      stderr: execution.stderr,
    },
  }
}

function runProcess(command, args, cwd, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env: {
        ...process.env,
        NO_COLOR: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    })

    let stdout = ""
    let stderr = ""
    let stdoutTruncated = false
    let stderrTruncated = false
    let timedOut = false

    const timer = setTimeout(() => {
      timedOut = true
      child.kill("SIGTERM")
      setTimeout(() => child.kill("SIGKILL"), 2_000).unref()
    }, timeoutMs)

    child.stdout.on("data", (chunk) => {
      const captured = captureChunk(stdout, chunk)
      stdout = captured.value
      stdoutTruncated ||= captured.truncated
    })

    child.stderr.on("data", (chunk) => {
      const captured = captureChunk(stderr, chunk)
      stderr = captured.value
      stderrTruncated ||= captured.truncated
    })

    child.on("error", (error) => {
      clearTimeout(timer)
      reject(error)
    })

    child.on("close", (exitCode, signal) => {
      clearTimeout(timer)
      resolve({
        exitCode,
        signal,
        stdout: appendTruncationNotice(stdout, stdoutTruncated),
        stderr: appendTruncationNotice(stderr, stderrTruncated),
        timedOut,
      })
    })
  })
}

function captureChunk(current, chunk) {
  if (current.length >= MAX_OUTPUT_CHARS) {
    return { value: current, truncated: true }
  }

  const next = current + chunk.toString("utf8")
  if (next.length <= MAX_OUTPUT_CHARS) {
    return { value: next, truncated: false }
  }

  return { value: next.slice(0, MAX_OUTPUT_CHARS), truncated: true }
}

function appendTruncationNotice(value, truncated) {
  if (!truncated) return value
  return `${value}\n\n[opencode-bridge truncated output at ${MAX_OUTPUT_CHARS} characters]`
}

function formatExecution(commandSummary, execution) {
  const sections = [
    `opencode command: ${commandSummary}`,
    `exit: ${execution.exitCode}${execution.signal ? ` signal=${execution.signal}` : ""}${execution.timedOut ? " timed_out=true" : ""}`,
  ]

  if (execution.stdout.trim()) {
    sections.push(`stdout:\n${execution.stdout.trimEnd()}`)
  }

  if (execution.stderr.trim()) {
    sections.push(`stderr:\n${execution.stderr.trimEnd()}`)
  }

  return sections.join("\n\n")
}

function summarizeCommand(command, args) {
  return [command, ...args.map((arg, index) => (index === args.length - 1 ? "<prompt>" : shellQuote(arg)))].join(" ")
}

function resolveDirectory(directoryInput) {
  const projectRoot = getProjectRoot()
  const directory = directoryInput
    ? path.resolve(projectRoot, directoryInput)
    : projectRoot

  if (process.env.OPENCODE_BRIDGE_ALLOW_OUTSIDE_PROJECT === "1") {
    return directory
  }

  const relative = path.relative(projectRoot, directory)
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(
      "directory must be inside CLAUDE_PROJECT_DIR unless OPENCODE_BRIDGE_ALLOW_OUTSIDE_PROJECT=1",
    )
  }

  return directory
}

function resolveProjectFile(fileInput, label, options = {}) {
  const projectRoot = getProjectRoot()
  const filePath = path.resolve(projectRoot, fileInput)
  const relative = path.relative(projectRoot, filePath)

  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`${label} must be inside CLAUDE_PROJECT_DIR`)
  }

  if (options.markdownOnly && path.extname(filePath).toLowerCase() !== ".md") {
    throw new Error(`${label} must point to a .md file`)
  }

  let stats
  try {
    stats = statSync(filePath)
  } catch {
    throw new Error(`${label} does not exist: ${relative}`)
  }

  if (!stats.isFile()) {
    throw new Error(`${label} must point to a file: ${relative}`)
  }

  return filePath
}

function getProjectRoot() {
  return path.resolve(process.env.CLAUDE_PROJECT_DIR || process.cwd())
}

function normalizeTimeout(value) {
  if (value === undefined) return DEFAULT_TIMEOUT_MS
  if (!Number.isInteger(value) || value < 1_000 || value > 1_800_000) {
    throw new Error("timeout_ms must be an integer between 1000 and 1800000")
  }
  return value
}

function requireString(value, name) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${name} is required`)
  }
  return value
}

function optionalString(value, name) {
  if (value === undefined || value === null || value === "") return undefined
  if (typeof value !== "string") {
    throw new Error(`${name} must be a string`)
  }
  return value
}

function validateAllowList(value, allowList, label, envName) {
  if (!value || !allowList) return
  if (!allowList.has(value)) {
    throw new Error(`${label} '${value}' is not allowed by ${envName}`)
  }
}

function readAllowList(name) {
  const value = process.env[name]
  if (!value) return undefined
  return new Set(value.split(",").map((item) => item.trim()).filter(Boolean))
}

function readIntegerEnv(name, fallback) {
  const value = process.env[name]
  if (!value) return fallback
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

function toolResult(text, isError, structuredContent) {
  return {
    content: [{ type: "text", text }],
    isError,
    ...(structuredContent ? { structuredContent } : {}),
  }
}

function writeResult(id, result) {
  write({ jsonrpc: "2.0", id, result })
}

function writeError(id, code, message) {
  write({ jsonrpc: "2.0", id, error: { code, message } })
}

function write(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`)
}

function shellQuote(value) {
  if (/^[A-Za-z0-9_./:=@+-]+$/.test(value)) return value
  return `'${value.replaceAll("'", "'\\''")}'`
}
