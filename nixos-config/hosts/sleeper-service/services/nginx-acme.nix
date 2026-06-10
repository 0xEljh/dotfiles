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

    virtualHosts = {
      # Ad hoc dev/staging entry points for common local app ports.
      "dev-3000.0xeljh.com" = mkProxyHost 3000;
      "dev-3001.0xeljh.com" = mkProxyHost 3001;
      "dev-5173.0xeljh.com" = mkProxyHost 5173;
      "dev-8000.0xeljh.com" = mkProxyHost 8000;
      "dev-19000.0xeljh.com" = mkProxyHost 19000;

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
