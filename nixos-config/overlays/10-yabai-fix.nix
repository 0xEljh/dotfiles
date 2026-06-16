# Fix yabai parallel build race condition (makefile races clean-build against
# linking under -j > 1, causing ENOENT on bin/yabai). Fixed in nixpkgs HEAD but
# applied here to avoid requiring a full nixpkgs bump.
self: super: {
  yabai = super.yabai.overrideAttrs (_: {
    enableParallelBuilding = false;
  });
}
