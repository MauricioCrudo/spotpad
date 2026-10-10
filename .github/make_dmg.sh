#!/bin/bash
# Arma un .dmg con la app y un acceso a Aplicaciones para arrastrarla.
#   make_dmg.sh <ruta/App.app> "<nombre del volumen>" <salida.dmg>
set -euo pipefail
APP="$1"; VOL="$2"; OUT="$3"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$(basename "$APP")"
ln -s /Applications "$STAGE/Applications"
# hdiutil a veces falla en los runners con «Resource busy»: reintentar
for i in 1 2 3 4 5; do
  if hdiutil create -volname "$VOL" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT"; then
    hdiutil verify "$OUT"
    exit 0
  fi
  echo "hdiutil falló (intento $i), reintento…"; sleep $((i * 5))
done
exit 1
