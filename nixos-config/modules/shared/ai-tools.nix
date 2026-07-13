{ config, pkgs, lib, ... }:

let
  huggingFaceCli = pkgs.python3Packages.huggingface-hub;
  playwrightCli = pkgs.callPackage ../../packages/playwright-cli { };
in
{
  home.sessionVariables = {
    # The built-in OpenCode search remains the zero-schema everyday path.
    # Do not set EXA_API_KEY: OpenCode sends that credential in the URL.
    OPENCODE_ENABLE_EXA = "1";
    CTX7_TELEMETRY_DISABLED = "1";
  };

  programs.zsh.initContent = lib.mkAfter ''
    opencode() (
      if [[ -r "$HOME/.config/ai-tools/secrets.env" ]]; then
        set -a
        source "$HOME/.config/ai-tools/secrets.env" || return
        set +a
      fi
      command opencode "$@"
    )

    claude() (
      if [[ -r "$HOME/.config/ai-tools/secrets.env" ]]; then
        set -a
        source "$HOME/.config/ai-tools/secrets.env" || return
        set +a
      fi
      command claude "$@"
    )
  '';

  home.activation.setupAITools = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    DOTFILES_DIR="$HOME/dotfiles"
    AI_TOOLS="$DOTFILES_DIR/ai-tools"
    HF_CLI=${huggingFaceCli}/bin/hf
    PLAYWRIGHT_CLI=${playwrightCli}/bin/playwright-cli
    PLAYWRIGHT_SKILL=${playwrightCli}/share/playwright-cli/skills/playwright-cli

    link_config() {
      local src="$1" dest="$2"
      if [ -e "$src" ]; then
        if [ -e "$dest" ] || [ -L "$dest" ]; then
          rm -rf "$dest"
        fi
        mkdir -p "$(dirname "$dest")"
        ln -sf "$src" "$dest"
        echo "Linked: $src -> $dest"
      else
        echo "Warning: Source not found: $src"
      fi
    }

    concat_with_separator() {
      local base="$1" ext="$2" out="$3"
      if [ -f "$base" ]; then
        mkdir -p "$(dirname "$out")"
        cat "$base" > "$out"
        [ -s "$ext" ] && printf '\n\n---\n\n' >> "$out" && cat "$ext" >> "$out"
        echo "Generated: $out"
      fi
    }

    link_skills() {
      local src="$1" dest="$2" skill name target
      if [ ! -d "$src" ]; then
        echo "Warning: Source not found: $src"
        return
      fi

      if [ -L "$dest" ] || { [ -e "$dest" ] && [ ! -d "$dest" ]; }; then
        rm -f "$dest"
      fi
      mkdir -p "$dest"
      for skill in "$src"/* "$src"/.[!.]*; do
        [ -e "$skill" ] || continue
        name="$(basename "$skill")"
        [ "$name" = "playwright-cli" ] && continue
        target="$dest/$name"
        if [ -e "$target" ] || [ -L "$target" ]; then
          rm -rf "$target"
        fi
        ln -s "$skill" "$target"
      done
      target="$dest/playwright-cli"
      if [ -e "$target" ] || [ -L "$target" ]; then
        rm -rf "$target"
      fi
      ln -s "$PLAYWRIGHT_SKILL" "$target"
      echo "Linked shared and Playwright CLI skills: $dest"
    }

    reconcile_claude_mcp() {
      local src="$1" dest="$HOME/.claude.json" tmp
      if [ -f "$src" ]; then
        mkdir -p "$(dirname "$dest")"
        [ -f "$dest" ] || printf '{}' > "$dest"
        tmp="$(mktemp "$dest.tmp.XXXXXX")"
        if ${pkgs.jq}/bin/jq -e '.mcpServers | type == "object"' "$src" >/dev/null \
          && ${pkgs.jq}/bin/jq -s '.[1].mcpServers as $managed | .[0] | .mcpServers = ($managed // {})' "$dest" "$src" > "$tmp"; then
          mv "$tmp" "$dest"
          echo "Reconciled Claude MCP: $src -> $dest"
        else
          rm -f "$tmp"
          echo "Warning: Failed to reconcile Claude MCP config: $src"
        fi
      else
        echo "Warning: Source not found: $src"
      fi
    }

    mkdir -p "$HOME/.config/ai-tools"
    if [ -f "$HOME/.config/ai-tools/secrets.env" ]; then
      chmod 600 "$HOME/.config/ai-tools/secrets.env"
    fi

    ARXIV_STORAGE="$HOME/.local/share/arxiv-mcp/papers"
    if [ -d "$ARXIV_STORAGE" ]; then
      ARXIV_KIB="$(${pkgs.coreutils}/bin/du -sk "$ARXIV_STORAGE" | ${pkgs.coreutils}/bin/cut -f1)"
      if [ "$ARXIV_KIB" -gt 2097152 ]; then
        echo "Warning: Local arXiv MCP storage exceeds its 2 GiB budget: $ARXIV_STORAGE"
      fi
    fi

    if ! "$PLAYWRIGHT_CLI" install-browser chromium; then
      echo "Warning: Failed to install the browser revision pinned by Playwright CLI"
    fi

    # OpenCode
    link_config "$AI_TOOLS/opencode/opencode.json" "$HOME/.config/opencode/opencode.json"
    link_skills "$AI_TOOLS/shared/skills"          "$HOME/.config/opencode/skills"
    link_config "$AI_TOOLS/opencode/agents"        "$HOME/.config/opencode/agents"
    link_config "$AI_TOOLS/opencode/commands"      "$HOME/.config/opencode/commands"
    concat_with_separator "$AI_TOOLS/shared/AI.md" "$AI_TOOLS/opencode/AGENTS.md" "$HOME/.config/opencode/AGENTS.md"

    # Claude Code
    link_config "$AI_TOOLS/claude-code/settings.json" "$HOME/.claude/settings.json"
    link_skills "$AI_TOOLS/shared/skills"             "$HOME/.claude/skills"
    link_config "$AI_TOOLS/claude-code/agents"        "$HOME/.claude/agents"
    reconcile_claude_mcp "$AI_TOOLS/claude-code/mcp.json"
    concat_with_separator "$AI_TOOLS/shared/AI.md" "$AI_TOOLS/claude-code/CLAUDE.md" "$HOME/.claude/CLAUDE.md"

    # Codex CLI: portable instructions and skills only, with no global MCPs.
    link_skills "$AI_TOOLS/shared/skills" "$HOME/.agents/skills"
    concat_with_separator "$AI_TOOLS/shared/AI.md" "$AI_TOOLS/codex/AGENTS.md" "$HOME/.codex/AGENTS.md"

    # Generate the official skill from the installed CLI so commands stay in
    # sync with the Nix-pinned huggingface_hub version. --claude installs the
    # canonical skill under ~/.agents and links Claude's legacy location, so
    # OpenCode and Codex discover the same generated skill.
    if ! "$HF_CLI" skills add --claude --global --force; then
      echo "Warning: Failed to install the Hugging Face CLI skill"
    fi

  '';
}
