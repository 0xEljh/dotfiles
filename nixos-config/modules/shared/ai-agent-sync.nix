{
  config,
  pkgs,
  lib,
  ...
}:
let
  cfg = config.aiTools.sharedAgents;
  package = pkgs.callPackage ../../packages/ai-agent-sync { };
  command = [
    "${package}/bin/ai-agents-sync"
    "--source-root"
    cfg.sourceRoot
    "--home"
    config.home.homeDirectory
  ];
  logDirectory = "${config.home.homeDirectory}/.local/state/ai-tools";
in
{
  options.aiTools.sharedAgents = {
    enable = lib.mkEnableOption "shared native agent installation on a qualified host";
    sourceRoot = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/dotfiles/ai-tools";
      description = "Live authored AI-tools directory, containing shared and native agents.";
    };
    watch.enable = lib.mkEnableOption "automatic shared agent synchronization";
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ package ];
    home.activation.syncSharedAgents =
      lib.hm.dag.entryBetween [ "setupLaunchAgents" ] [ "setupAITools" ]
        ''
          mkdir -p ${lib.escapeShellArg logDirectory}
          ${lib.escapeShellArgs command}
        '';

    systemd.user.services.ai-agent-sync =
      lib.mkIf (cfg.watch.enable && pkgs.stdenv.hostPlatform.isLinux)
        {
          Unit.Description = "Synchronize shared coding agents";
          Service = {
            Type = "simple";
            ExecStart = lib.escapeShellArgs (command ++ [ "--watch" ]);
            Restart = "on-failure";
            RestartSec = 5;
            WorkingDirectory = config.home.homeDirectory;
          };
          Install.WantedBy = [ "default.target" ];
        };

    launchd.agents.ai-agent-sync = lib.mkIf (cfg.watch.enable && pkgs.stdenv.hostPlatform.isDarwin) {
      enable = true;
      config = {
        ProgramArguments = command ++ [ "--watch" ];
        RunAtLoad = true;
        KeepAlive = true;
        ThrottleInterval = 5;
        StandardOutPath = "${logDirectory}/agent-sync.log";
        StandardErrorPath = "${logDirectory}/agent-sync.log";
      };
    };
  };
}
