{ user }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.prMaintenanceWorker;
  homeDir = "/home/${user}";
  configFile = "/etc/pr-maintenance/worker.json";
  stateDir = "${homeDir}/.local/state/pr-maintenance";
  json = pkgs.formats.json { };
  maintenancePackage = pkgs.callPackage ../../../packages/pr-maintenance { };
  normalOpenCode = config.home-manager.users.${user}.aiTools.opencodePackage;
  settings = {
    state_dir = stateDir;
    checkout_root = "${stateDir}/checkouts";
    opencode_server = "http://127.0.0.1:8779";
    opencode_binary = "${cfg.opencodePackage}/bin/opencode";
    worker_executable = "${worker}/bin/pr-worker";
    skills_dir = "${homeDir}/dotfiles/ai-tools/shared/skills";
  }
  // cfg.settings;

  # Also used by noninteractive SSH: it must not depend on a login-shell PATH.
  worker = pkgs.writeShellApplication {
    name = "pr-worker";
    runtimeInputs = [
      maintenancePackage
      cfg.opencodePackage
      pkgs.git
      pkgs.gh
      pkgs.openssh
      pkgs.systemd
      pkgs.coreutils
      pkgs.ripgrep
      pkgs.fd
      pkgs.bash
      pkgs.zsh
      pkgs.gnused
      pkgs.gnugrep
      pkgs.nodejs_24
      pkgs.bun
      pkgs.fzf
    ];
    text = ''
      if [ "$(id -un)" != ${lib.escapeShellArg user} ]; then
        echo "pr-worker must run as ${user}" >&2
        exit 78
      fi
      umask 077
      export HOME=${lib.escapeShellArg homeDir}
      export PATH="$PATH:${homeDir}/.nix-profile/bin:/etc/profiles/per-user/${user}/bin:/run/current-system/sw/bin:/run/wrappers/bin"
      export XDG_CONFIG_HOME="''${XDG_CONFIG_HOME:-$HOME/.config}"
      export XDG_CACHE_HOME="''${XDG_CACHE_HOME:-$HOME/.cache}"
      export XDG_DATA_HOME="''${XDG_DATA_HOME:-$HOME/.local/share}"
      export XDG_STATE_HOME="''${XDG_STATE_HOME:-$HOME/.local/state}"
      export XDG_RUNTIME_DIR="''${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
      export DBUS_SESSION_BUS_ADDRESS="''${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
      exec ${maintenancePackage}/bin/pr-worker "$@"
    '';
  };

  userService = {
    unitConfig.ConditionUser = user;
    environment = {
      HOME = homeDir;
      NIX_LD = "/run/current-system/sw/share/nix-ld/lib/ld.so";
      NIX_LD_LIBRARY_PATH = "/run/current-system/sw/share/nix-ld/lib";
      LD_LIBRARY_PATH = "/run/current-system/sw/share/nix-ld/lib:/usr/lib/wsl/lib";
      TRITON_LIBCUDA_PATH = "/usr/lib/wsl/lib";
    };
    serviceConfig = {
      WorkingDirectory = homeDir;
      EnvironmentFile = [ "-${homeDir}/.config/ai-tools/secrets.env" ];
      UMask = "0077";
      Restart = "no";
      KillMode = "control-group";
    };
  };
in
{
  options.services.prMaintenanceWorker = {
    enable = lib.mkEnableOption "the opt-in PR maintenance worker in the normal user account";

    package = lib.mkOption {
      type = lib.types.package;
      readOnly = true;
      default = worker;
      description = "Worker wrapper with pinned runtime dependencies, also available for targeted deployment.";
    };

    settings = lib.mkOption {
      type = lib.types.attrsOf json.type;
      default = { };
      description = ''
        Non-secret worker JSON settings. Enabling requires explicit execution_enabled,
        rollout_acknowledged, runner_version, and the chosen assurance acknowledgement.
        The shared OpenCode account is not a credential or discussion-write sandbox.
      '';
    };

    opencodePackage = lib.mkOption {
      type = lib.types.package;
      default = normalOpenCode;
      defaultText = lib.literalExpression "config.home-manager.users.${user}.aiTools.opencodePackage";
      description = "Pinned runner package, shared with the normal OpenCode instance.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/" settings.worker_executable;
        message = "prMaintenanceWorker requires a fixed absolute worker_executable for evidence helpers.";
      }
      {
        assertion = cfg.settings.execution_enabled or false;
        message = "prMaintenanceWorker requires settings.execution_enabled = true.";
      }
      {
        assertion = cfg.settings.rollout_acknowledged or false;
        message = "prMaintenanceWorker requires explicit acknowledgement of shared-account execution and credential exposure.";
      }
      {
        assertion =
          if (cfg.settings.quiescence_mode or "strict") == "best_effort" then
            cfg.settings.best_effort_acknowledged or false
          else
            (cfg.settings.quiescence_mode or "strict") == "strict"
            && (cfg.settings.runner_contract_verified or false);
        message = "prMaintenanceWorker requires verified strict probes or explicit acknowledgement of best-effort shared-instance observations.";
      }
      {
        assertion = (cfg.settings.runner_version or "") == "opencode v${cfg.opencodePackage.version}";
        message = "prMaintenanceWorker settings.runner_version must explicitly match the pinned runner's exact --version output (opencode v${cfg.opencodePackage.version}).";
      }
      {
        assertion = toString cfg.opencodePackage == toString normalOpenCode;
        message = "prMaintenanceWorker must use the same OpenCode package as the normal CLI and service.";
      }
    ];

    environment.systemPackages = [ worker ];
    environment.etc."pr-maintenance/worker.json".source =
      json.generate "pr-maintenance-worker.json" settings;
    systemd = {
      tmpfiles.rules = [ "d ${stateDir} 0700 ${user} users -" ];
      user = {
        services = {
          # No automatic restart: a started marker means inspect, not rerun the prompt.
          "pr-maintenance@" = lib.recursiveUpdate userService {
            description = "PR maintenance attempt %i";
            after = [ "opencode-serve.service" ];
            serviceConfig = {
              Type = "exec";
              ExecStart = "${worker}/bin/pr-worker --config ${configFile} execute %i";
            };
          };

          pr-maintenance-reconcile = lib.recursiveUpdate userService {
            description = "Reconcile accepted PR maintenance attempts without blind replay";
            serviceConfig = {
              Type = "oneshot";
              ExecStart = "${worker}/bin/pr-worker --config ${configFile} reconcile";
              TimeoutStartSec = "2min";
            };
          };
        };

        timers.pr-maintenance-reconcile = {
          description = "Reconcile PR maintenance worker state every minute";
          unitConfig.ConditionUser = user;
          wantedBy = [ "timers.target" ];
          timerConfig = {
            OnBootSec = "2min";
            OnUnitActiveSec = "1min";
            Unit = "pr-maintenance-reconcile.service";
          };
        };
      };
    };
  };
}
