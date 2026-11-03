_final: prev:

{
  gh = prev.gh.overrideAttrs (_old: {
    version = "2.99.0";
    src = prev.fetchFromGitHub {
      owner = "cli";
      repo = "cli";
      tag = "v2.99.0";
      hash = "sha256-+66P7F+UBhqV+B/ak1LqzK8X5z+z9PLN2XhIB9FJyPg=";
    };
    vendorHash = "sha256-bVc4dhDapAp1YtO06C/nSrdxllpEhVFC2iZNPmjsJkI=";
  });
}
