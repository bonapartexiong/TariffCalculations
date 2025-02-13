{ pkgs ? import <nixpkgs> {} }:

pkgs.stdenv.mkDerivation {
  name = "python-app";
  src = ./backend;

  buildInputs = [
    pkgs.python310
    pkgs.python310Packages.pip
    pkgs.python310Packages.virtualenv
  ];

  installPhase = ''
    virtualenv venv
    source venv/bin/activate
    pip install -r requirements.txt
  '';
}