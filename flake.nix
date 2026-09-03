{
  description = "driftwm-desktop: Modular, spatial desktop icons manager for DriftWM";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    let
      overlay = final: prev: {
        driftwm-desktop = final.callPackage ./package.nix { };
      };
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
      in
      {
        packages = {
          driftwm-desktop = pkgs.driftwm-desktop;
          default = pkgs.driftwm-desktop;
        };

        apps = {
          driftwm-desktop = flake-utils.lib.mkApp {
            drv = pkgs.driftwm-desktop;
            exePath = "/bin/driftwm-desktop";
          };
          default = self.apps.${system}.driftwm-desktop;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [
              ps.pyqt5
              ps.dbus-python
            ]))
            pkgs.qt5.qtwayland
            pkgs.qt5.wrapQtAppsHook
            pkgs.xdg-utils
            pkgs.glib
          ];
        };
      }
    ) // {
      overlays.default = overlay;

      nixosModules.default = { config, lib, pkgs, ... }:
        with lib;
        let
          cfg = config.services.driftwm-desktop;
        in
        {
          options.services.driftwm-desktop = {
            enable = mkEnableOption "DriftWM Desktop Icons";
            package = mkPackageOption pkgs "driftwm-desktop" {
              default = [ "driftwm-desktop" ];
            };
          };

          config = mkIf cfg.enable {
            environment.systemPackages = [ cfg.package ];
          };
        };
    };
}
