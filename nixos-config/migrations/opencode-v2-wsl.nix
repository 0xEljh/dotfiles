{
  home-manager.users.elijah = { config, lib, ... }: {
    aiTools.opencodeV2.enable = true;
    services.t3Serve.t3Version = lib.mkForce "0.0.46-nightly.20261014.3093";
    assertions = [
      {
        assertion = config.services.t3Serve.t3Package == null;
        message = "The OpenCode 2 candidate requires the pinned upstream T3 nightly.";
      }
    ];
  };
}
