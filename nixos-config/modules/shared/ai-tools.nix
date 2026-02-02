{ config, pkgs, lib, ... }:

{
  home.activation.setupAITools = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    DOTFILES_DIR="$HOME/dotfiles"
    AI_TOOLS="$DOTFILES_DIR/ai-tools"

    link_config() {
      local src="$1" dest="$2"
      if [ -e "$src" ]; then
        [ -e "$dest" ] || [ -L "$dest" ] && rm -rf "$dest"
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
        [ -f "$ext" ] && printf '\n\n---\n\n' >> "$out" && cat "$ext" >> "$out"
        echo "Generated: $out"
      fi
    }

    # OpenCode
    link_config "$AI_TOOLS/opencode/opencode.json" "$HOME/.config/opencode/opencode.json"
    link_config "$AI_TOOLS/shared/skills"          "$HOME/.config/opencode/skills"
    link_config "$AI_TOOLS/opencode/agents"        "$HOME/.config/opencode/agents"
    link_config "$AI_TOOLS/opencode/commands"      "$HOME/.config/opencode/commands"
    concat_with_separator "$AI_TOOLS/shared/AI.md" "$AI_TOOLS/opencode/AGENTS.md" "$HOME/.config/opencode/AGENTS.md"

    # Claude Code
    link_config "$AI_TOOLS/claude-code/settings.json" "$HOME/.claude/settings.json"
    link_config "$AI_TOOLS/shared/skills"             "$HOME/.claude/skills"
    link_config "$AI_TOOLS/claude-code/agents"        "$HOME/.claude/agents"
    concat_with_separator "$AI_TOOLS/shared/AI.md" "$AI_TOOLS/claude-code/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
  '';
}
