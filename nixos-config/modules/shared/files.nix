{ pkgs, config, ... }:

{
  # Initializes Emacs with org-mode so we can tangle the main config
  # ".emacs.d/init.el" = {
  #   text = builtins.readFile ../shared/config/emacs/init.el;
  # };

  # IMPORTANT: The Emacs configuration expects a config.org file at ~/.config/emacs/config.org
  # You can either:
  # 1. Copy the provided config.org to ~/.config/emacs/config.org
  # 2. Set EMACS_CONFIG_ORG environment variable to point to your config.org location
  # 3. Uncomment below to have Nix manage the file:
  #
  # ".config/emacs/config.org" = {
  #   text = builtins.readFile ../shared/config/emacs/config.org;
  # };

  ".config/kitty/ssh.conf" = {
    source = ../../../kitty/ssh.conf;
  };

  ".config/kitty/remote-shell.sh" = {
    executable = true;
    text = ''
      #!/bin/sh
      set -e

      # Try nix shell (flakes-style) first
      if command -v nix >/dev/null 2>&1; then
        if nix shell --help >/dev/null 2>&1; then
          export KITTY_REMOTE_NIX=flakes
          exec nix \
            --extra-experimental-features nix-command \
            --extra-experimental-features flakes \
            shell \
              nixpkgs#zsh \
              nixpkgs#zoxide \
              nixpkgs#lazygit \
              nixpkgs#bat \
              nixpkgs#atuin \
              nixpkgs#eza \
              nixpkgs#difftastic \
              nixpkgs#fzf \
              nixpkgs#neovim \
              nixpkgs#tmux \
            --command zsh -l
        fi
      fi

      # Fallback to nix-shell (legacy)
      if command -v nix-shell >/dev/null 2>&1; then
        export KITTY_REMOTE_NIX=legacy
        exec nix-shell -p \
          zsh zoxide lazygit bat atuin eza difftastic fzf neovim tmux \
          --command "zsh -l"
      fi

      # No nix, try system zsh
      if command -v zsh >/dev/null 2>&1; then
        exec zsh -l
      fi

      # Fallback to user's default shell
      if [ -n "$SHELL" ] && [ -x "$SHELL" ]; then
        exec "$SHELL" -l
      fi

      # Last resort
      exec /bin/sh -l
    '';
  };

  ".config/kitty/remote-zsh/.zshrc" = {
    text = ''
      # Kitty SSH remote configuration loaded
      export KITTY_REMOTE_CONFIG=1

      export PATH=$HOME/.pnpm-packages/bin:$HOME/.pnpm-packages:$PATH
      export PATH=$HOME/.npm-packages/bin:$HOME/bin:$PATH
      export PATH=$HOME/.local/share/bin:$PATH

      export HISTIGNORE="pwd:ls:cd"

      export ALTERNATE_EDITOR=""
      export EDITOR="vim"
      export VISUAL="vim"

      alias diff=difft

      alias ls="eza --group --icons";
      alias ll="eza -la --group --git --icons";
      alias la="eza -a --group --icons";
      alias lt="eza -T --level=2 --git-ignore --icons";
      alias tree="eza -T --icons";

      if command -v zoxide >/dev/null 2>&1; then
        # Initialize zoxide with cd replacement so 'cd' uses zoxide
        eval "$(zoxide init zsh --cmd cd)"
      fi

      if command -v atuin >/dev/null 2>&1; then
        eval "$(atuin init zsh)"
      fi

      # Minimal prompt with indicator that kitty config is loaded
      autoload -Uz colors && colors
      setopt PROMPT_SUBST

      # Build indicator based on how we got here
      if [ -n "$KITTY_REMOTE_NIX" ]; then
        # nix tools available (flakes or legacy)
        _kitty_indicator="%F{green}[kitty+nix]%f"
      else
        # kitty config loaded but no nix
        _kitty_indicator="%F{green}[kitty]%f"
      fi

      PROMPT="''${_kitty_indicator} %F{blue}%n@%m%f:%F{yellow}%~%f$ "

      # Show config loaded message on first shell startup
      if [ -z "$KITTY_CONFIG_SHOWN" ]; then
        export KITTY_CONFIG_SHOWN=1
        if [ -n "$KITTY_REMOTE_NIX" ]; then
          echo "\033[32m[kitty]\033[0m Remote config loaded (nix tools available)"
        else
          echo "\033[32m[kitty]\033[0m Remote config loaded"
        fi
      fi
    '';
  };
}
