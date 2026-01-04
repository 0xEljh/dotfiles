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

      if command -v nix >/dev/null 2>&1; then
        if nix shell --help >/dev/null 2>&1; then
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

      if command -v nix-shell >/dev/null 2>&1; then
        exec nix-shell -p \
          zsh zoxide lazygit bat atuin eza difftastic fzf neovim tmux \
          --command "zsh -l"
      fi

      if command -v zsh >/dev/null 2>&1; then
        exec zsh -l
      fi

      if [ -n "$SHELL" ] && [ -x "$SHELL" ]; then
        exec "$SHELL" -l
      fi

      exec /bin/sh -l
    '';
  };

  ".config/kitty/remote-zsh/.zshrc" = {
    text = ''
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
        eval "$(zoxide init zsh)"
      fi

      if command -v atuin >/dev/null 2>&1; then
        eval "$(atuin init zsh)"
      fi
    '';
  };
}
