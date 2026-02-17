{ config, pkgs, lib, ... }:

let name = "elijah";
    user = "elijah";
    email = "elijahng96@gmail.com"; in
{
  # Shared shell configuration
  zsh = {
    enable = true;
    autocd = false;
    plugins = [
      {
        name = "powerlevel10k";
        src = pkgs.zsh-powerlevel10k;
        file = "share/zsh-powerlevel10k/powerlevel10k.zsh-theme";
      }
      {
        name = "powerlevel10k-config";
        src = lib.cleanSource ./config;
        file = "p10k.zsh";
      }
    ];

    initContent = ''
      if [[ -f /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
        . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
        . /nix/var/nix/profiles/default/etc/profile.d/nix.sh
      fi

      # Define variables for directories
      export PATH=$HOME/.pnpm-packages/bin:$HOME/.pnpm-packages:$PATH
      export PATH=$HOME/.npm-packages/bin:$HOME/bin:$PATH
      export PATH=$HOME/.local/share/bin:$PATH

      # Remove history data we don't want to see
      export HISTIGNORE="pwd:ls:cd"

      # vim, later neovim as editor
      export ALTERNATE_EDITOR=""
      export EDITOR="vim"
      export VISUAL="vim"

      # nix shortcuts
      shell() {
          nix-shell '<nixpkgs>' -A "$1"
      }

      # Use difftastic, syntax-aware diffing
      alias diff=difft

      # replace other aliases
      alias ls="eza --group --icons";
      alias ll="eza -la --group --git --icons";
      alias la="eza -a --group --icons";
      alias lt="eza -T --level=2 --git-ignore --icons";
      alias tree="eza -T --icons";
      alias ncat="notion-cat";

      # zoxide
      eval "$(zoxide init zsh)"

      # atuin
      if command -v atuin >/dev/null 2>&1; then
        eval "$(atuin init zsh)"
      fi
    '';
  };

  git = {
    enable = true;
    ignores = [ "*.swp" ];
    lfs = {
      enable = true;
    };
    settings = {
      user.name = "0xEljh";
      user.email = "elijahng96@gmail.com";
      init.defaultBranch = "main";
      core = {
        editor = "vim";
        autocrlf = "input";
      };
      pull.rebase = true;
      rebase.autoStash = true;
      diff.external = "${pkgs.difftastic}/bin/difft";
      pager.diff = "";
      pager.show = "";
    };
  };

  vim = {
    enable = true;
    plugins = with pkgs.vimPlugins; [ vim-airline vim-airline-themes vim-startify vim-tmux-navigator ];
    settings = { ignorecase = true; };
    extraConfig = ''
      "" General
      set number
      set history=1000
      set nocompatible
      set modelines=0
      set encoding=utf-8
      set scrolloff=3
      set showmode
      set showcmd
      set hidden
      set wildmenu
      set wildmode=list:longest
      set cursorline
      set ttyfast
      set nowrap
      set ruler
      set backspace=indent,eol,start
      set laststatus=2
      set clipboard=autoselect

      " Dir stuff
      set nobackup
      set nowritebackup
      set noswapfile
      set backupdir=~/.config/vim/backups
      set directory=~/.config/vim/swap

      " Relative line numbers for easy movement
      set relativenumber
      set rnu

      "" Whitespace rules
      set tabstop=8
      set shiftwidth=2
      set softtabstop=2
      set expandtab

      "" Searching
      set incsearch
      set gdefault

      "" Statusbar
      set nocompatible " Disable vi-compatibility
      set laststatus=2 " Always show the statusline
      let g:airline_theme='bubblegum'
      let g:airline_powerline_fonts = 1

      "" Local keys and such
      let mapleader=","
      let maplocalleader=" "

      "" Change cursor on mode
      :autocmd InsertEnter * set cul
      :autocmd InsertLeave * set nocul

      "" File-type highlighting and configuration
      syntax on
      filetype on
      filetype plugin on
      filetype indent on

      "" Paste from clipboard
      nnoremap <Leader>, "+gP

      "" Copy from clipboard
      xnoremap <Leader>. "+y

      "" Move cursor by display lines when wrapping
      nnoremap j gj
      nnoremap k gk

      "" Map leader-q to quit out of window
      nnoremap <leader>q :q<cr>

      "" Move around split
      nnoremap <C-h> <C-w>h
      nnoremap <C-j> <C-w>j
      nnoremap <C-k> <C-w>k
      nnoremap <C-l> <C-w>l

      "" Easier to yank entire line
      nnoremap Y y$

      "" Move buffers
      nnoremap <tab> :bnext<cr>
      nnoremap <S-tab> :bprev<cr>

      "" Like a boss, sudo AFTER opening the file to write
      cmap w!! w !sudo tee % >/dev/null

      let g:startify_lists = [
        \ { 'type': 'dir',       'header': ['   Current Directory '. getcwd()] },
        \ { 'type': 'sessions',  'header': ['   Sessions']       },
        \ { 'type': 'bookmarks', 'header': ['   Bookmarks']      }
        \ ]

      let g:startify_bookmarks = [
        \ '~/Projects',
        \ '~/Documents',
        \ ]

      let g:airline_theme='bubblegum'
      let g:airline_powerline_fonts = 1
      '';
     };

  alacritty = {
    enable = true;
    settings = {
      cursor = {
        style = "Block";
      };

      window = {
        opacity = 1.0;
        padding = {
          x = 24;
          y = 24;
        };
      };

      font = {
        normal = {
          family = "MesloLGS NF";
          style = "Regular";
        };
        size = lib.mkMerge [
          (lib.mkIf pkgs.stdenv.hostPlatform.isLinux 10)
          (lib.mkIf pkgs.stdenv.hostPlatform.isDarwin 14)
        ];
      };

      colors = {
        primary = {
          background = "0x1f2528";
          foreground = "0xc0c5ce";
        };

        normal = {
          black = "0x1f2528";
          red = "0xec5f67";
          green = "0x99c794";
          yellow = "0xfac863";
          blue = "0x6699cc";
          magenta = "0xc594c5";
          cyan = "0x5fb3b3";
          white = "0xc0c5ce";
        };

        bright = {
          black = "0x65737e";
          red = "0xec5f67";
          green = "0x99c794";
          yellow = "0xfac863";
          blue = "0x6699cc";
          magenta = "0xc594c5";
          cyan = "0x5fb3b3";
          white = "0xd8dee9";
        };
      };
    };
  };

  ssh = {
    enable = true;
    # Disable default config values that will be removed in future home-manager
    enableDefaultConfig = false;
    includes = lib.mkDefault [
      "${config.home.homeDirectory}/.ssh/config_external"
    ];
    matchBlocks = {
      "*" = {
        serverAliveInterval = 60;
        serverAliveCountMax = 30;
      };

      # Example SSH configuration for GitHub
      # "github.com" = {
      #   identitiesOnly = true;
      #   identityFile = [
      #     (lib.mkIf pkgs.stdenv.hostPlatform.isLinux
      #       "/home/${user}/.ssh/id_github"
      #     )
      #     (lib.mkIf pkgs.stdenv.hostPlatform.isDarwin
      #       "/Users/${user}/.ssh/id_github"
      #     )
      #   ];
      # };
    };
  };

  tmux = {
    enable = true;
    plugins = with pkgs.tmuxPlugins; [
      vim-tmux-navigator
      sensible
      yank
      prefix-highlight
      {
        plugin = power-theme;
        extraConfig = ''
           set -g @tmux_power_theme 'gold'
        '';
      }
      {
        plugin = resurrect; # Used by tmux-continuum

        # Use XDG data directory
        # https://github.com/tmux-plugins/tmux-resurrect/issues/348
        extraConfig = ''
          set -g @resurrect-dir '$HOME/.cache/tmux/resurrect'
          set -g @resurrect-capture-pane-contents 'on'
          set -g @resurrect-pane-contents-area 'visible'
        '';
      }
      {
        plugin = continuum;
        extraConfig = ''
          set -g @continuum-restore 'on'
          set -g @continuum-save-interval '5' # minutes
        '';
      }
    ];
    terminal = "screen-256color";
    prefix = "C-x";
    escapeTime = 10;
    historyLimit = 50000;
    extraConfig = ''
      # Remove Vim mode delays
      set -g focus-events on

      # Allow escape sequences to pass through to the outer terminal (tmux 3.2+)
      # This enables window title propagation over SSH with kitten ssh
      set -g allow-passthrough on

      set -g set-titles on
      set -g set-titles-string "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command} #{pane_title} #{pane_current_path}"

      set -g prefix2 C-b

      # Enable full mouse support
      set -g mouse on

      # -----------------------------------------------------------------------------
      # Key bindings
      # -----------------------------------------------------------------------------

      # Unbind default keys
      unbind '"'
      unbind %

      bind-key -T prefix C-b send-prefix -2

      # Split panes, vertical or horizontal
      bind-key x split-window -v
      bind-key v split-window -h

      # Move around panes with vim-like bindings (h,j,k,l)
      bind-key -n M-k select-pane -U
      bind-key -n M-h select-pane -L
      bind-key -n M-j select-pane -D
      bind-key -n M-l select-pane -R

      # Smart pane switching with awareness of Vim splits.
      # This is copy paste from https://github.com/christoomey/vim-tmux-navigator
      is_vim="ps -o state= -o comm= -t '#{pane_tty}' \
        | grep -iqE '^[^TXZ ]+ +(\\S+\\/)?g?(view|n?vim?x?)(diff)?$'"
      bind-key -n 'C-h' if-shell "$is_vim" 'send-keys C-h'  'select-pane -L'
      bind-key -n 'C-j' if-shell "$is_vim" 'send-keys C-j'  'select-pane -D'
      bind-key -n 'C-k' if-shell "$is_vim" 'send-keys C-k'  'select-pane -U'
      bind-key -n 'C-l' if-shell "$is_vim" 'send-keys C-l'  'select-pane -R'
      tmux_version='$(tmux -V | sed -En "s/^tmux ([0-9]+(.[0-9]+)?).*/\1/p")'
      if-shell -b '[ "$(echo "$tmux_version < 3.0" | bc)" = 1 ]' \
        "bind-key -n 'C-\\' if-shell \"$is_vim\" 'send-keys C-\\'  'select-pane -l'"
      if-shell -b '[ "$(echo "$tmux_version >= 3.0" | bc)" = 1 ]' \
        "bind-key -n 'C-\\' if-shell \"$is_vim\" 'send-keys C-\\\\'  'select-pane -l'"

      bind-key -T copy-mode-vi 'C-h' select-pane -L
      bind-key -T copy-mode-vi 'C-j' select-pane -D
      bind-key -T copy-mode-vi 'C-k' select-pane -U
      bind-key -T copy-mode-vi 'C-l' select-pane -R
      bind-key -T copy-mode-vi 'C-\\' select-pane -l

      # Enable extended keys (CSI u) support for shift+enter and other modified keys
      set -s extended-keys on
      set -s user-keys[0] "\x1b[13;2u"
      bind-key -n User0 send-keys Escape "[13;2u"

      # -----------------------------------------------------------------------------
      # Mouse selection and copy mode improvements
      # -----------------------------------------------------------------------------

      # Keep selection highlighted after copying (don't auto-clear)
      set -g @yank_action 'copy-pipe-no-clear'

      # Exit copy-mode when clicking elsewhere (not just q/Esc)
      bind -T copy-mode MouseDown1Pane select-pane \; send-keys -X clear-selection
      bind -T copy-mode-vi MouseDown1Pane select-pane \; send-keys -X clear-selection

      # Double-click to select words (works in and out of copy-mode)
      bind -T copy-mode DoubleClick1Pane select-pane \; send-keys -X select-word \; send-keys -X copy-pipe-no-clear
      bind -T copy-mode-vi DoubleClick1Pane select-pane \; send-keys -X select-word \; send-keys -X copy-pipe-no-clear
      bind -n DoubleClick1Pane select-pane \; copy-mode -M \; send-keys -X select-word \; send-keys -X copy-pipe-no-clear

      # Triple-click to select lines (works in and out of copy-mode)
      bind -T copy-mode TripleClick1Pane select-pane \; send-keys -X select-line \; send-keys -X copy-pipe-no-clear
      bind -T copy-mode-vi TripleClick1Pane select-pane \; send-keys -X select-line \; send-keys -X copy-pipe-no-clear
      bind -n TripleClick1Pane select-pane \; copy-mode -M \; send-keys -X select-line \; send-keys -X copy-pipe-no-clear
      '';
    };

  kitty = {
    enable = true;

    ## CORE LOOK & FEEL ------------------------------------------------------
    font = {
      name = "MesloLGS NF";
      size = lib.mkDefault 14;      # bump on Hi-DPI if needed
    };

    # Catppuccin Mocha (already present in kitty-themes)
    themeFile = "Catppuccin-Mocha";

    ## GENERAL SETTINGS ------------------------------------------------------
    settings = {
      confirm_os_window_close = 0;
      enable_audio_bell       = "no";
      allow_remote_control    = "socket-only";     # let ssh sessions control parent kitty
      listen_on               = "unix:/tmp/mykitty";
      macos_option_as_alt     = "yes";     # natural ⌥ behaviour on macOS
      scrollback_lines        = 10000;
      wheel_scroll_multiplier = 3.0;

      ## window title - show running command for ActivityWatch tracking
      # {title} contains the shell-set title OR the foreground process name
      # This helps ActivityWatch detect tools like opencode, nvim, etc.
      shell_integration       = "enabled";  # enables title reporting from shell

      # Allow tmux/remote apps to set kitty window/tab titles via OSC escape sequences
      # This enables tmux window titles to propagate to kitty tabs
      allow_hyperlinks        = "yes";
      allow_cloning           = "ask";

      ## aesthetics
      background_opacity      = "0.80";
      background_blur         = 40;        # macOS only
      hide_window_decorations = "titlebar-and-corners";
      window_padding_width    = 8;
      inactive_text_alpha     = "0.80";
      inactive_border_color   = "#1e1e2e"; # i.e. background for mocha
      active_border_color     = "#cba6f7";  # Mauve accent

      ## tab bar
      tab_bar_edge            = "top";
      tab_bar_min_tabs        = 2;
      tab_title_template      = "{index}:{title}";
      active_tab_foreground   = "#1e1e2e"; # background
      active_tab_background   = "#a6e3a1";
    };

    ## KEYBINDINGS -----------------------------------------------------------
    keybindings = {
      # new windows / tabs
      "cmd+shift+enter" = "new_os_window_with_cwd";
      "cmd+shift+t"     = "new_tab_with_cwd";

      # pane navigation (bsp-style, mirrors yabai bindings)
      "alt+h"           = "neighbor left";
      "alt+l"           = "neighbor right";
      "alt+k"           = "neighbor up";
      "alt+j"           = "neighbor down";

      # tab cycling
      "cmd+]"           = "next_tab";
      "cmd+["           = "previous_tab";

      # copy / paste – works in tmux too
      "cmd+shift+c"     = "copy_to_clipboard";
      "cmd+shift+v"     = "paste_from_clipboard";

      # shift+enter - send raw CSI u escape sequence so apps can distinguish from plain enter
      # Using send_text with the literal escape sequence ensures it works through SSH host jumps
      # \x1b[13;2u = CSI u encoding for shift+enter (13 = enter keycode, 2 = shift modifier)
      "shift+enter"     = "send_text all \\x1b[13;2u";
    };
  };
  
}
