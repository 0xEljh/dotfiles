import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentHarnessPortfolioTests(unittest.TestCase):
    def load_json(self, relative_path: str) -> dict:
        with (ROOT / relative_path).open(encoding="utf-8") as config_file:
            return json.load(config_file)

    def test_opencode_scopes_research_mcps(self) -> None:
        config = self.load_json("ai-tools/opencode/opencode.json")

        self.assertEqual({"arxiv", "exa", "parallel"}, set(config["mcp"]))
        self.assertEqual("allow", config["permission"]["*"])
        for server in ("arxiv", "exa", "parallel"):
            self.assertEqual("deny", config["permission"][f"{server}_*"])

        arxiv = config["mcp"]["arxiv"]
        self.assertIn("arxiv-mcp-server[pdf]==0.5.0", arxiv["command"])
        self.assertEqual("https://mcp.exa.ai/mcp", config["mcp"]["exa"]["url"])
        self.assertEqual(
            "{env:EXA_MCP_API_KEY}",
            config["mcp"]["exa"]["headers"]["x-api-key"],
        )
        self.assertEqual(
            "https://search.parallel.ai/mcp", config["mcp"]["parallel"]["url"]
        )

    def test_research_agents_are_read_only_and_have_matching_mcps(self) -> None:
        opencode_agent = (
            ROOT / "ai-tools/opencode/agents/research.md"
        ).read_text(encoding="utf-8")
        claude_agent = (
            ROOT / "ai-tools/claude-code/agents/research.md"
        ).read_text(encoding="utf-8")

        for permission in ("arxiv_*", "exa_*", "parallel_*"):
            self.assertIn(f'"{permission}": allow', opencode_agent)
        self.assertIn("edit: deny", opencode_agent)
        self.assertIn("bash: deny", opencode_agent)

        for server in ("arxiv", "exa", "parallel"):
            self.assertIn(f"- {server}:", claude_agent)
            self.assertIn(f"mcp__{server}__*", claude_agent)
        self.assertIn("tools: Read, Glob, Grep, WebSearch, WebFetch", claude_agent)
        self.assertIn('x-api-key: "${EXA_MCP_API_KEY:-}"', claude_agent)
        self.assertIn("arxiv-mcp-server[pdf]==0.5.0", claude_agent)

    def test_claude_has_no_global_mcp_servers(self) -> None:
        config = self.load_json("ai-tools/claude-code/mcp.json")
        self.assertEqual({}, config["mcpServers"])

    def test_playwright_cli_is_pinned_with_a_lockfile(self) -> None:
        package = self.load_json("nixos-config/packages/playwright-cli/package.json")
        lock = self.load_json("nixos-config/packages/playwright-cli/package-lock.json")

        self.assertEqual("0.1.17", package["dependencies"]["@playwright/cli"])
        self.assertEqual(
            "0.1.17",
            lock["packages"]["node_modules/@playwright/cli"]["version"],
        )
        self.assertEqual(
            "1.62.0-alpha-1783623505000",
            lock["packages"]["node_modules/playwright-core"]["version"],
        )

    def test_context7_cli_and_skill_are_pinned(self) -> None:
        package = self.load_json("nixos-config/packages/context7-cli/package.json")
        lock = self.load_json("nixos-config/packages/context7-cli/package-lock.json")
        skill = (ROOT / "ai-tools/shared/skills/find-docs/SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertEqual("0.5.4", package["dependencies"]["ctx7"])
        self.assertEqual("0.5.4", lock["packages"]["node_modules/ctx7"]["version"])
        self.assertIn("ctx7 library", skill)
        self.assertIn("ctx7 docs", skill)
        self.assertNotIn("ctx7@latest", skill)

    def test_specialist_clis_are_wired_without_global_mcps(self) -> None:
        packages = (ROOT / "nixos-config/modules/shared/packages.nix").read_text(
            encoding="utf-8"
        )
        ai_tools = (ROOT / "nixos-config/modules/shared/ai-tools.nix").read_text(
            encoding="utf-8"
        )

        self.assertIn("context7Cli", packages)
        self.assertIn("python3Packages.huggingface-hub", packages)
        self.assertIn("llm-agents.codex", packages)
        self.assertIn('CTX7_TELEMETRY_DISABLED = "1";', ai_tools)
        self.assertIn('"$HF_CLI" skills add --claude --global --force', ai_tools)
        self.assertIn('"$HOME/.agents/skills"', ai_tools)
        self.assertIn('"$HOME/.codex/AGENTS.md"', ai_tools)
        link_skills = ai_tools.split("link_skills() {", 1)[1].split("\n    }", 1)[0]
        self.assertNotIn('rm -rf "$dest"', link_skills)

    def test_secret_delivery_does_not_commit_or_embed_exa_key(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        example = (ROOT / "ai-tools/secrets.env.example").read_text(
            encoding="utf-8"
        )
        ai_tools = (ROOT / "nixos-config/modules/shared/ai-tools.nix").read_text(
            encoding="utf-8"
        )
        opencode = self.load_json("ai-tools/opencode/opencode.json")

        self.assertIn("secrets.env", gitignore)
        self.assertIn("EXA_MCP_API_KEY=", example)
        self.assertNotRegex(example, r"EXA_MCP_API_KEY=.+")
        self.assertNotIn('EXA_API_KEY = "', ai_tools)
        self.assertEqual(
            "{env:EXA_MCP_API_KEY}",
            opencode["mcp"]["exa"]["headers"]["x-api-key"],
        )
        self.assertNotIn("?exaApiKey=", opencode["mcp"]["exa"]["url"])

    def test_nix_wiring_reconciles_removals_and_avoids_mcp_browser_env(self) -> None:
        ai_tools = (ROOT / "nixos-config/modules/shared/ai-tools.nix").read_text(
            encoding="utf-8"
        )
        t3_serve = (ROOT / "nixos-config/modules/shared/t3-serve.nix").read_text(
            encoding="utf-8"
        )

        reconcile_filter = (
            ".[1].mcpServers as $managed | .[0] | "
            ".mcpServers = ($managed // {})"
        )
        self.assertIn(reconcile_filter, ai_tools)
        self.assertIn('OPENCODE_ENABLE_EXA = "1";', ai_tools)
        self.assertNotIn("PLAYWRIGHT_MCP_EXECUTABLE_PATH", ai_tools)
        self.assertNotIn("PLAYWRIGHT_MCP_EXECUTABLE_PATH", t3_serve)

        old = {
            "auth": {"token": "preserved"},
            "mcpServers": {"playwright": {"command": "npx"}},
        }
        managed = {"mcpServers": {}}
        result = subprocess.run(
            ["jq", "-s", reconcile_filter],
            input=f"{json.dumps(old)}\n{json.dumps(managed)}\n",
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            {"auth": {"token": "preserved"}, "mcpServers": {}},
            json.loads(result.stdout),
        )

    def test_public_arxiv_service_is_pinned(self) -> None:
        service = (
            ROOT / "nixos-config/hosts/sleeper-service/services/web-apps.nix"
        ).read_text(encoding="utf-8")
        self.assertIn("arxiv-mcp-server[pdf]==0.5.0", service)


if __name__ == "__main__":
    unittest.main()
