{ lib
, stdenv
, python3
, makeWrapper
, qt5
, xdg-utils
, glib
}:

let
  pythonEnv = python3.withPackages (ps: with ps; [
    pyqt5
    dbus-python
  ]);
in
stdenv.mkDerivation {
  pname = "driftwm-desktop";
  version = "0.1.0";

  src = ./.;

  nativeBuildInputs = [
    makeWrapper
    qt5.wrapQtAppsHook
  ];

  buildInputs = [
    pythonEnv
    qt5.qtwayland
    qt5.qtbase
  ];

  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin $out/lib/driftwm-desktop
    cp -r driftwm_desktop $out/lib/driftwm-desktop/
    cp driftwm-desktop $out/lib/driftwm-desktop/driftwm-desktop
    cp desktop_launchers.py $out/lib/driftwm-desktop/desktop_launchers.py

    makeWrapper ${pythonEnv}/bin/python3 $out/bin/driftwm-desktop \
      --add-flags "$out/lib/driftwm-desktop/driftwm-desktop" \
      --prefix PATH : ${lib.makeBinPath [ xdg-utils glib ]}

    ln -s $out/bin/driftwm-desktop $out/bin/desktop-launchers
    ln -s $out/bin/driftwm-desktop $out/bin/desktop_launchers.py

    runHook postInstall
  '';

  dontWrapQtApps = false;
  preFixup = ''
    wrapQtApp "$out/bin/driftwm-desktop"
  '';

  meta = with lib; {
    description = "Modular, spatial desktop icons manager for DriftWM";
    license = licenses.gpl3Only;
    platforms = platforms.linux;
    mainProgram = "driftwm-desktop";
  };
}
