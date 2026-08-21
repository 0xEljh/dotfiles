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
        self.assertEqual(
            "Bearer {env:PARALLEL_MCP_API_KEY}",
            config["mcp"]["parallel"]["headers"]["Authorization"],
        )

    def test_research_agents_are_read_only_and_have_matching_mcps(self) -> None:
        opencode_agent = (
            ROOT / "ai-tools/opencode/agents/researcher.md"
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
        self.assertIn(
            'Authorization: "Bearer ${PARALLEL_MCP_API_KEY:-}"', claude_agent
        )
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
        self.assertIn("PARALLEL_MCP_API_KEY=", example)
        self.assertNotRegex(example, r"PARALLEL_MCP_API_KEY=.+")
        self.assertNotIn('EXA_API_KEY = "', ai_tools)
        self.assertEqual(
            "{env:EXA_MCP_API_KEY}",
            opencode["mcp"]["exa"]["headers"]["x-api-key"],
        )
        self.assertNotIn("?exaApiKey=", opencode["mcp"]["exa"]["url"])
        self.assertEqual(
            "Bearer {env:PARALLEL_MCP_API_KEY}",
            opencode["mcp"]["parallel"]["headers"]["Authorization"],
        )

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
        self.assertIn("unset OPENCODE_SERVER_USERNAME", t3_serve)
        self.assertIn("unset OPENCODE_SERVER_PASSWORD", t3_serve)

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

    def test_retired_kodo_services_are_disabled_and_private(self) -> None:
        services = (
            ROOT / "nixos-config/hosts/sleeper-service/services/web-apps.nix"
        ).read_text(encoding="utf-8")
        self.assertIn("kodo-api = {\n      enable = false;", services)
        self.assertIn("kodo-ml = {\n      enable = false;", services)

        for relative_path in (
            "nixos-config/hosts/sleeper-service/services/nginx-acme.nix",
            "nixos-config/hosts/sleeper-service/services/telegram-bot.nix",
            "scripts/personal_telegram_bot/personal_telegram_bot/config.py",
        ):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("kodo-api", text, relative_path)
            self.assertNotIn("kodo-ml", text, relative_path)

        self.assertTrue(
            (ROOT / "nixos-config/secrets/sleeper-service/kodo-api.env").exists()
        )

    def test_public_dev_3000_requires_sops_managed_basic_auth(self) -> None:
        nginx = (
            ROOT / "nixos-config/hosts/sleeper-service/services/nginx-acme.nix"
        ).read_text(encoding="utf-8")
        secrets = (
            ROOT / "nixos-config/hosts/sleeper-service/secrets.nix"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'basicAuthFile = "/run/dev-3000-auth/current/htpasswd";',
            nginx,
        )
        self.assertIn('sops.secrets."dev-3000.password"', secrets)
        self.assertNotIn('sops.secrets."dev-3000.htpasswd"', secrets)
        self.assertTrue(
            (
                ROOT
                / "nixos-config/secrets/sleeper-service/dev-3000-auth.yaml"
            ).exists()
        )

    def test_arxiv_pins_the_mcp_sdk_below_2(self) -> None:
        """arxiv-mcp-server 0.5.0 declares `mcp>=1.27.0` with no upper bound.

        Resolving mcp 2.0.0 crashes it at import with
        `AttributeError: 'Server' object has no attribute 'list_prompts'`,
        so every call site must constrain the SDK itself.
        """
        opencode = self.load_json("ai-tools/opencode/opencode.json")
        command = opencode["mcp"]["arxiv"]["command"]
        self.assertEqual("mcp<2", command[command.index("--with") + 1])

        for relative_path in (
            "ai-tools/claude-code/agents/research.md",
            "nixos-config/hosts/sleeper-service/services/web-apps.nix",
        ):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("mcp<2", text, relative_path)

    def test_llm_agents_use_supported_platforms_and_current_cache(self) -> None:
        flake = (ROOT / "nixos-config/flake.nix").read_text(encoding="utf-8")
        hosts = [
            ROOT / "nixos-config/hosts/wsl/default.nix",
            ROOT / "nixos-config/hosts/sleeper-service/default.nix",
            ROOT / "nixos-config/hosts/darwin/default.nix",
        ]

        self.assertIn('darwinSystems = [ "aarch64-darwin" ];', flake)
        self.assertNotIn('llm-agents.inputs.nixpkgs.follows = "nixpkgs"', flake)
        for host in hosts:
            config = host.read_text(encoding="utf-8")
            self.assertIn("https://cache.numtide.com", config)
            self.assertIn(
                "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g=",
                config,
            )
            self.assertNotIn("numtide.cachix.org", config)

    def test_t3_is_aligned_on_the_pinned_nightly(self) -> None:
        module = (ROOT / "nixos-config/modules/shared/t3-serve.nix").read_text(
            encoding="utf-8"
        )
        linux_hosts = [
            ROOT / "nixos-config/modules/wsl/home-manager.nix",
            ROOT / "nixos-config/modules/sleeper-service/home-manager.nix",
        ]
        casks = (ROOT / "nixos-config/modules/darwin/casks.nix").read_text(
            encoding="utf-8"
        )

        self.assertIn('default = "0.0.29-nightly.20260725.899";', module)
        for host in linux_hosts:
            config = host.read_text(encoding="utf-8")
            self.assertIn("useTailscaleServe = true;", config)
            self.assertIn("tailscaleServePort = 443;", config)
            self.assertNotIn("t3Package = \"file:", config)
        self.assertIn('"t3-code@nightly"', casks)
        self.assertNotIn('"t3-code"', casks)
        self.assertIn('"chatgpt"', casks)
        self.assertIn('"opencode-desktop"', casks)

    def test_remote_opencode_is_loopback_only_and_fails_closed(self) -> None:
        module = (
            ROOT / "nixos-config/modules/shared/opencode-serve.nix"
        ).read_text(encoding="utf-8")
        wsl = (ROOT / "nixos-config/modules/wsl/home-manager.nix").read_text(
            encoding="utf-8"
        )
        sleeper = (
            ROOT / "nixos-config/modules/sleeper-service/home-manager.nix"
        ).read_text(encoding="utf-8")
        secrets = (ROOT / "ai-tools/secrets.env.example").read_text(
            encoding="utf-8"
        )

        self.assertIn("useTailscaleServe", module)
        self.assertIn("OPENCODE_SERVER_PASSWORD must be set", module)
        self.assertIn("tailscale serve --bg --yes --https=", module)
        self.assertIn("tailscale serve --https=", module)
        for config in (wsl, sleeper):
            self.assertIn('host = "127.0.0.1";', config)
            self.assertIn("useTailscaleServe = true;", config)
            self.assertIn("tailscaleServePort = 8779;", config)
        self.assertIn("OPENCODE_SERVER_USERNAME=opencode", secrets)
        self.assertIn("OPENCODE_SERVER_PASSWORD=", secrets)
        self.assertNotRegex(secrets, r"OPENCODE_SERVER_PASSWORD=.+")


if __name__ == "__main__":
    unittest.main()
