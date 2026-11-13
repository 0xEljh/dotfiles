import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    server: { deps: { inline: ["ai-sdk-provider-claude-code"] } },
  },
})
