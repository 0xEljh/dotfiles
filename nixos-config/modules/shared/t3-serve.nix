{ config, lib, pkgs, ... }:

let
  cfg = config.services.t3Serve;
  t3PackageSpec =
    if cfg.t3Package == null then "t3@${cfg.t3Version}" else cfg.t3Package;
  t3PackageArg = lib.escapeShellArg t3PackageSpec;

  staticArgs =
    if cfg.useTailscaleServe then [
      "serve"
      "--tailscale-serve"
      "--tailscale-serve-port"
      (toString cfg.tailscaleServePort)
    ] else [
      "serve"
      "--host"
      cfg.host
      "--port"
      (toString cfg.port)
    ];

  staticArgString = lib.concatStringsSep " " (map lib.escapeShellArg staticArgs);

  needsTailscale = cfg.useTailscaleServe || cfg.bindToTailscaleIp;

  t3Wrapper = pkgs.writeShellApplication {
    name = "t3-serve-wrapper";
    # node-pty (a t3 transitive dep) runs `sh` and a small build toolchain in
    # its npm postinstall. Systemd user services start with an empty PATH on
    # NixOS, so we have to bring our own coreutils / bash / build deps.
    runtimeInputs = [
      pkgs.nodejs_24
      pkgs.bun
      pkgs.bash
      pkgs.coreutils
      pkgs.gnumake
      pkgs.gcc
      pkgs.python3
    ] ++ lib.optional needsTailscale pkgs.tailscale;
    text = ''
      export NPM_CONFIG_YES=true
      export NPM_CONFIG_LOGLEVEL=warn
      echo ${lib.escapeShellArg "t3-serve: using ${t3PackageSpec}"}
      # Pre-warm the npx cache so the first real run doesn't hang on download.
      npx -y ${t3PackageArg} --version >/dev/null 2>&1 || true
    '' + (if cfg.bindToTailscaleIp && !cfg.useTailscaleServe then ''
      # Resolve the tailnet IPv4 at start time so renaming the tailnet or
      # changing MagicDNS hostnames doesn't require a rebuild.
      TSIP=""
      for _ in $(seq 1 30); do
        TSIP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
        if [ -n "$TSIP" ]; then break; fi
        sleep 1
      done
      if [ -z "$TSIP" ]; then
        echo "t3-serve: could not resolve tailscale IPv4 within 30s" >&2
        exit 1
      fi
      exec npx -y ${t3PackageArg} serve --host "$TSIP" --port ${toString cfg.port}
    '' else ''
      exec npx -y ${t3PackageArg} ${staticArgString}
    '');
  };
in
{
  options.services.t3Serve = {
    enable = lib.mkEnableOption "T3 Code headless agent server (`t3 serve`)";

    useTailscaleServe = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Bind the server via `tailscale serve`, exposing it over the tailnet
        with TLS. Requires HTTPS certs to be enabled in the tailnet admin.
        When false, bind to `host`:`port` directly (or use `bindToTailscaleIp`).
      '';
    };

    bindToTailscaleIp = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Resolve the host's tailnet IPv4 at unit start via `tailscale ip -4`
        and bind there on `port`. Plain HTTP, tailnet-only reachable, and
        survives tailnet rename / MagicDNS changes. Ignored if
        `useTailscaleServe = true`.
      '';
    };

    tailscaleServePort = lib.mkOption {
      type = lib.types.port;
      default = 8443;
      description = "Port for `--tailscale-serve` to listen on (HTTPS).";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Host to bind when `useTailscaleServe = false`.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 3773;
      description = "Port to bind when `useTailscaleServe = false` (T3 Code default 3773).";
    };

    t3Version = lib.mkOption {
      type = lib.types.str;
      default = "0.0.27";
      description = ''
        npm tag or version of `t3` to run via `npx` when `t3Package` is unset.
        Keep this pinned to avoid alpha churn.
      '';
    };

    t3Package = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "@your-scope/t3-open-code-fix@0.0.27-pr2673-2811";
      description = ''
        Full npm package spec to run via `npx`. When set, this overrides
        `t3Version` and allows targeted patched packages or aliases.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.user.services.t3-serve = {
      Unit = {
        Description = "T3 Code headless agent server (npx t3 serve)";
        Documentation = [ "https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md" ];
        Wants = [ "network-online.target" ];
        After = [ "network-online.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${t3Wrapper}/bin/t3-serve-wrapper";
        Restart = "on-failure";
        RestartSec = 5;
        WorkingDirectory = "%h";
        Environment = [
          "HOME=%h"
          "XDG_CONFIG_HOME=%h/.config"
          "XDG_CACHE_HOME=%h/.cache"
        ];
      };

      Install.WantedBy = [ "default.target" ];
    };
  };
}
