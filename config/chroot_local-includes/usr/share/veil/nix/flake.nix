# Veil OS optional tool bundles.
#
# nixpkgs is pinned in flake.lock. Binaries come from cache.nixos.org and are
# accepted only with a valid signature (require-sigs = true in nix.conf).
# Install with:  veil install bundle:<name>
{
  description = "Veil OS optional security tool bundles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/f7e804372523bedd74d273b173de45c8e02c6d9a";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      # Only bundles explicitly marked "unfree" (Burp Suite) use this set,
      # and `veil install` asks the user to accept the license first.
      pkgsUnfree = import nixpkgs { inherit system; config.allowUnfree = true; };
      bundles = builtins.fromJSON (builtins.readFile ./bundles.json);
      extra = p: {
        llama-cpp-vulkan = p.llama-cpp.override { vulkanSupport = true; };
      };
      pick = p: name: (extra p).${name} or p.${name};
      mk = name: b:
        let p = if (b.unfree or false) then pkgsUnfree else pkgs;
        in p.buildEnv {
          name = "veil-bundle-${name}";
          paths = map (pick p) b.packages;
          ignoreCollisions = true;
        };
    in {
      packages.${system} = builtins.listToAttrs (map
        (n: { name = "bundle-${n}"; value = mk n bundles.${n}; })
        (builtins.attrNames bundles));
    };
}
