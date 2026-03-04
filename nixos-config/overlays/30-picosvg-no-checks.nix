self: super:
{
  python3 = super.python3.override {
    packageOverrides = final: prev: {
      picosvg = prev.picosvg.overridePythonAttrs (_: {
        doCheck = false;
      });
    };
  };

  python3Packages = self.python3.pkgs;
}
