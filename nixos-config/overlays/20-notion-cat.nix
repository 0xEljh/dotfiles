self: super:

let
  uv = super.uv;
in
{
  notion-cat = super.writeShellApplication {
    name = "notion-cat";
    runtimeInputs = [ uv ];
    text = ''
      set -euo pipefail

      DOTFILES_DIR="''${DOTFILES_DIR:-$HOME/dotfiles}"
      SCRIPT_PATH="$DOTFILES_DIR/scripts/notion_cat.py"

      if [ ! -f "$SCRIPT_PATH" ]; then
        echo "notion-cat: script not found at $SCRIPT_PATH" >&2
        exit 1
      fi

      exec ${uv}/bin/uv run "$SCRIPT_PATH" "$@"
    '';
  };
}
