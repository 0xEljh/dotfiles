{ config, lib, pkgs, ... }:

let
  cfg = config.services.opencodeServe;

  homeDir = config.home.homeDirectory;
  user = config.home.username;

  # opencode is provided by the llm-agents flake overlay (see
  # modules/shared/packages.nix). Default to that package so the served
  # binary matches the one on the user's PATH.
  opencodePkg =
    if cfg.package != null then cfg.package
    else if (pkgs ? llm-agents && pkgs.llm-agents ? opencode) then pkgs.llm-agents.opencode
    else pkgs.opencode;

  opencodeWrapper = pkgs.writeShellApplication {
    name = "opencode-serve-wrapper";
    # opencode is an agent: it shells out to git (snapshots), ripgrep, the
    # user's shell, node, and whatever a project's tools need. Systemd user
    # services start with an empty PATH on NixOS, so we bring the core tools
    # explicitly AND append the user profile so project toolchains resolve
    # exactly as they do in an interactive shell.
    runtimeInputs = [
      opencodePkg
      pkgs.git
      pkgs.ripgrep
      pkgs.fd
      pkgs.bash
      pkgs.zsh
      pkgs.coreutils
      pkgs.gnused
      pkgs.gnugrep
      pkgs.nodejs_24
      pkgs.bun
      pkgs.gh
      pkgs.fzf
    ] ++ lib.optional cfg.useTailscaleServe pkgs.tailscale;
    text = ''
      # Append the user profile paths so opencode sees every tool the user
      # has installed (uv, cargo, docker, python, ...), not just the core set.
      export PATH="$PATH:${homeDir}/.nix-profile/bin:/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin:/run/wrappers/bin"
    '' + lib.optionalString cfg.useTailscaleServe ''
      if [ -z "''${OPENCODE_SERVER_PASSWORD:-}" ]; then
        echo "opencode-serve: OPENCODE_SERVER_PASSWORD must be set for Tailscale Serve" >&2
        exit 78
      fi

      tailscale serve --bg --yes --https=${toString cfg.tailscaleServePort} \
        http://127.0.0.1:${toString cfg.port}
    '' + ''
      exec opencode serve --hostname ${cfg.host} --port ${toString cfg.port}
    '';
  };
in
{
  options.services.opencodeServe = {
    enable = lib.mkEnableOption "opencode headless agent server (`opencode serve`)";

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Host/interface to bind. Localhost-only by default (the server is unsecured unless OPENCODE_SERVER_PASSWORD is set).";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8779;
      description = "Port for `opencode serve` to listen on.";
    };

    useTailscaleServe = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Expose the loopback server over Tailnet-only HTTPS with Tailscale
        Serve. OPENCODE_SERVER_PASSWORD must be present in the service
        environment or startup fails closed.
      '';
    };

    tailscaleServePort = lib.mkOption {
      type = lib.types.port;
      default = 8779;
      description = "Tailnet HTTPS port that proxies to the OpenCode server.";
    };

    extraEnvironment = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Additional systemd environment entries for host-specific runtimes.";
    };

    package = lib.mkOption {
      type = lib.types.nullOr lib.types.package;
      default = null;
      description = "opencode package to serve. Defaults to the llm-agents overlay's opencode.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = !cfg.useTailscaleServe || cfg.host == "127.0.0.1";
        message = "services.opencodeServe.useTailscaleServe requires a 127.0.0.1 backend";
      }
    ];

    systemd.user.services.opencode-serve = {
      Unit = {
        Description = "opencode headless agent server (opencode serve)";
        Documentation = [ "https://opencode.ai/docs/server/" ];
        Wants = [ "network-online.target" ];
        After = [ "network-online.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${opencodeWrapper}/bin/opencode-serve-wrapper";
        Restart = "on-failure";
        RestartPreventExitStatus = [ 78 ];
        RestartSec = 5;
        WorkingDirectory = "%h";
        Environment = [
          "HOME=%h"
          "XDG_CONFIG_HOME=%h/.config"
          "XDG_CACHE_HOME=%h/.cache"
          "XDG_DATA_HOME=%h/.local/share"
          "XDG_STATE_HOME=%h/.local/state"
          "OPENCODE_ENABLE_EXA=1"
          "OPENCODE_CLAUDE_CLI=${pkgs.llm-agents.claude-code}/bin/claude"
          "CTX7_TELEMETRY_DISABLED=1"
        ] ++ cfg.extraEnvironment;
        EnvironmentFile = [ "-%h/.config/ai-tools/secrets.env" ];
      } // lib.optionalAttrs cfg.useTailscaleServe {
        ExecStopPost = "-${pkgs.tailscale}/bin/tailscale serve --https=${toString cfg.tailscaleServePort} off";
      };

      Install.WantedBy = [ "default.target" ];
    };
  };
}
