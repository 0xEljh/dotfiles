{ ... }:

let
  # Flip to false after the first successful staging issuance, then rebuild.
  # Verify success via:
  #   sudo journalctl -u acme-0xeljh-wildcard.service -n 200 --no-pager
  # Staging certs are not browser-trusted; this flag exists so a misconfigured
  # token can't burn the Let's Encrypt prod rate limit.
  useStaging = true;
in
{
  security.acme = {
    acceptTerms = true;
    defaults.email = "elijah@0xeljh.com";

    # Wildcard cert covering 0xeljh.com and *.0xeljh.com via NameSilo DNS-01.
    # Named distinctly from any per-vhost HTTP-01 cert so a staging issuance
    # can never clobber a working production cert on disk. nginx vhosts get
    # cut over to this cert in a follow-up phase, not this commit.
    certs."0xeljh-wildcard" = {
      domain = "0xeljh.com";
      extraDomainNames = [ "*.0xeljh.com" ];
      dnsProvider = "namesilo";
      credentialsFile = "/var/lib/secrets/acme-0xeljh.env";
      # Use a public resolver to follow propagation from NameSilo's
      # authoritative nameservers (DNSOWL) rather than the box's local
      # resolver, which may cache stale negatives.
      dnsResolver = "1.1.1.1:53";
      server =
        if useStaging
        then "https://acme-staging-v02.api.letsencrypt.org/directory"
        else null;
    };
  };
}
