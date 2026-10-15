#!/bin/bash
# Crea el certificado propio con el que el CI firma SpotPad para Mac y lo guarda como secreto del repo.
# Se corre UNA vez, a mano, en la Mac (necesita `gh` con sesión iniciada).
#
# Para qué: sin firma propia cada versión es «otra app» para macOS y hay que volver a dar el permiso de
# Accesibilidad (Consolidar) después de cada actualización. Con el mismo certificado en todas las
# versiones, el permiso queda. No hace falta cuenta de Apple: macOS solo compara que sea el mismo.
#
# Deja una copia en ~/Documents/Programacion/SpotPad-firma (fuera del repo). Si se pierde, se corre de
# nuevo: cambia el certificado y hay que dar el permiso una vez más.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="$HOME/Documents/Programacion/SpotPad-firma"
if [ -e "$DEST/firma.p12" ]; then
  echo "Ya existe $DEST/firma.p12. Para regenerarlo, borralo primero (vas a tener que dar el permiso de nuevo)."
  exit 1
fi
mkdir -p "$DEST"; chmod 700 "$DEST"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cert.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = SpotPad Firma
[ext]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
EOF
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$TMP/key.pem" -out "$DEST/cert.pem" -days 7300 -config "$TMP/cert.cnf" 2>/dev/null
PASS=$(openssl rand -hex 16)
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$DEST/cert.pem" -out "$DEST/firma.p12" -passout "pass:$PASS" -name "SpotPad Firma"
printf '%s' "$PASS" > "$DEST/clave.txt"
chmod 600 "$DEST"/*
base64 -i "$DEST/firma.p12" | gh secret set MAC_CERT_P12
printf '%s' "$PASS" | gh secret set MAC_CERT_PASS
echo "Listo: secretos MAC_CERT_P12 y MAC_CERT_PASS cargados en el repo. Copia en $DEST"
