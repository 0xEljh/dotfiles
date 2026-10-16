import { checkClaudeLogin } from "./auth-v2.js"
import { AUTH_SENTINEL } from "./policy.js"

// OpenCode stores trimmed stdout as a key credential, not a JSON document.
try {
  const login = await checkClaudeLogin()
  if (login.available) {
    process.stdout.write(AUTH_SENTINEL + "\n")
  } else {
    process.stderr.write(login.reason + "\n")
    process.exitCode = 1
  }
} catch {
  process.stderr.write("Claude Agent login verification failed\n")
  process.exitCode = 1
}
