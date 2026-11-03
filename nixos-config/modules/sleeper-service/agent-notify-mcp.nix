{ config, lib, pkgs, ... }:

let
  cfg = config.services.agentNotifyMcp;
  botDir = "/home/elijah/dotfiles/scripts/personal_telegram_bot";
  environmentFile = if cfg.environmentFile == null then "" else cfg.environmentFile;
in
{
  options.services.agentNotifyMcp = {
    enable = lib.mkEnableOption "the fixed SSH stdio launcher for owner Telegram notifications";
    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = ''
        Absolute runtime credential path readable by elijah. The file is not
        copied into the Nix store. Confirm its source and permissions before enabling.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/" environmentFile && !(lib.hasPrefix "/nix/store/" environmentFile);
        message = "services.agentNotifyMcp.environmentFile must be an explicit absolute runtime path outside the Nix store.";
      }
    ];

    home.packages = [
      (pkgs.writeShellApplication {
        name = "agent-notify-mcp";
        text = ''
          cd ${lib.escapeShellArg botDir}
          if [[ ! -r ${lib.escapeShellArg environmentFile} || ! -x .venv/bin/python || ! -x .venv/bin/botctl ]]; then
            echo "Notification credentials or prepared virtual environment unavailable" >&2
            exit 1
          fi
          # Ignore caller argv and environment, including uv and credential overrides.
          exec ${pkgs.coreutils}/bin/env -i \
            HOME=/home/elijah \
            PATH=${pkgs.coreutils}/bin \
            UV_PROJECT_ENVIRONMENT=${lib.escapeShellArg "${botDir}/.venv"} \
            ${pkgs.uv}/bin/uv run --frozen --offline --no-sync \
              --no-config --no-env-file --no-python-downloads --quiet \
              --python ${lib.escapeShellArg "${botDir}/.venv/bin/python"} \
              botctl serve-notify-mcp --env-file ${lib.escapeShellArg environmentFile}
        '';
      })
    ];
  };
}
