{ pkgs ? import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/22.11.tar.gz") {} }:

pkgs.mkShell {
  buildInputs = [
    pkgs.python310
    pkgs.python310Packages.virtualenv
    pkgs.zlib.dev
    pkgs.libffi.dev
    pkgs.openssl.dev
    pkgs.gcc
    pkgs.libxml2.dev
    pkgs.libxslt.dev
    pkgs.stdenv.cc.cc.lib
  ];

  shellHook = ''
    export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH
  '';
}