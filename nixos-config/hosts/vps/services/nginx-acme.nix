{ ... }:

let
  mkNextHost = port: {
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
      "dev.nyf1.com" = mkNextHost 3000;
      "dev2.nyf1.com" = mkNextHost 3001;

      "0xeljh.com" = mkNextHost 3005;
      "www.0xeljh.com" = mkRedirectHost "0xeljh.com";

      "teathegathering.com" = mkNextHost 3006;
      "www.teathegathering.com" = mkRedirectHost "teathegathering.com";

      "vamptutor.com" = mkNextHost 3007;
      "www.vamptutor.com" = mkRedirectHost "vamptutor.com";
      "binderapi.vamptutor.com" = mkNextHost 38127;
    };
  };

  security.acme = {
    acceptTerms = true;
    defaults.email = "elijah@0xeljh.com";
  };

  networking.firewall.allowedTCPPorts = [ 80 443 ];
}
