{ ... }:

let
  mkProxyHost = port: {
    enableACME = true;
    forceSSL = true;
    locations."/" = {
      proxyPass = "http://127.0.0.1:${toString port}";
      proxyWebsockets = true;
    };
  };

  mkMcpProxyHost = port: {
    enableACME = true;
    forceSSL = true;
    locations = {
      "= /mcp".extraConfig = "return 307 /mcp/;";
      "/mcp/" = {
        proxyPass = "http://127.0.0.1:${toString port}";
        extraConfig = ''
          limit_req zone=mcp burst=20 nodelay;
          proxy_buffering off;
          proxy_cache off;
          proxy_read_timeout 1h;
          proxy_send_timeout 1h;
        '';
      };
      "/".extraConfig = "return 404;";
    };
  };

  mkRedirectHost = canonicalHost: {
    enableACME = true;
    forceSSL = true;
    globalRedirect = canonicalHost;
  };
in
{
  services.nginx = {
    enable = true;
    recommendedGzipSettings = true;
    recommendedOptimisation = true;
    recommendedProxySettings = true;
    recommendedTlsSettings = true;

    # Per-source-IP cap for the public webhook vhost. A phone sends a handful
    # of events a day; 10 r/s (burst 20) is invisible to it but bounds abuse.
    appendHttpConfig = ''
      limit_req_zone $binary_remote_addr zone=hooks:1m rate=10r/s;
      limit_req_zone $binary_remote_addr zone=mcp:1m rate=2r/s;
    '';

    virtualHosts = {
      # Ad hoc dev/staging entry points for common local app ports.
      "dev-3000.0xeljh.com" = mkProxyHost 3000;
      "dev-3001.0xeljh.com" = mkProxyHost 3001;
      "dev-5173.0xeljh.com" = mkProxyHost 5173;
      "dev-8000.0xeljh.com" = mkProxyHost 8000;
      "dev-19000.0xeljh.com" = mkProxyHost 19000;

      # Public Streamable HTTP MCP endpoint for arXiv research tools.
      # ChatGPT developer-mode connectors should use:
      #   https://arxiv-mcp.0xeljh.com/mcp
      "arxiv-mcp.0xeljh.com" = mkMcpProxyHost 18003;

      # Kodo Go API. SSE stream endpoint must not be buffered, so it gets a
      # dedicated regex location that overrides proxy_buffering for that one route.
      "kodo-api.0xeljh.com" = {
        enableACME = true;
        forceSSL = true;
        locations = {
          "~ ^/v1/messages/[^/]+/stream$" = {
            proxyPass = "http://127.0.0.1:18002";
            extraConfig = ''
              proxy_buffering off;
              proxy_cache off;
              proxy_read_timeout 1h;
              proxy_send_timeout 1h;
            '';
          };
          "/" = {
            proxyPass = "http://127.0.0.1:18002";
          };
        };
      };

      # Phone life-event webhooks (Sleep as Android, later MacroDroid/OwnTracks).
      # The auth token rides in the URL path because Sleep as Android cannot set
      # headers, so access_log is off to keep it out of logs (the app logs each
      # request to journald with the token redacted). Rate-limited via the
      # `hooks` zone below; the app still validates token + payload itself.
      "hooks.0xeljh.com" = {
        enableACME = true;
        forceSSL = true;
        extraConfig = "access_log off;";
        locations."/" = {
          proxyPass = "http://127.0.0.1:8830";
          extraConfig = "limit_req zone=hooks burst=20 nodelay;";
        };
      };

      "0xeljh.com" = mkProxyHost 3005;
      "www.0xeljh.com" = mkRedirectHost "0xeljh.com";

      "teathegathering.com" = mkProxyHost 3006;
      "www.teathegathering.com" = mkRedirectHost "teathegathering.com";

      "vamptutor.com" = mkProxyHost 3007;
      "www.vamptutor.com" = mkRedirectHost "vamptutor.com";
      "binderapi.vamptutor.com" = mkProxyHost 38127;
    };
  };

  networking.firewall.allowedTCPPorts = [ 80 443 ];
}
