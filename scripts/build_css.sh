#!/usr/bin/env sh
# Compila app/static/css/app.css con el binario standalone de Tailwind (sin Node.js).
# Uso: scripts/build_css.sh [--watch]
set -eu
cd "$(dirname "$0")/.."
VERSION="v3.4.17"
BIN="${TAILWIND_BIN:-.tools/tailwindcss}"
if [ ! -x "$BIN" ]; then
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64) ASSET=tailwindcss-linux-x64 ;;
    Linux-aarch64) ASSET=tailwindcss-linux-arm64 ;;
    Darwin-arm64) ASSET=tailwindcss-macos-arm64 ;;
    Darwin-x86_64) ASSET=tailwindcss-macos-x64 ;;
    *) echo "Plataforma no soportada; define TAILWIND_BIN" >&2; exit 1 ;;
  esac
  mkdir -p "$(dirname "$BIN")"
  curl -sfL -o "$BIN" "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/$ASSET"
  chmod +x "$BIN"
fi
exec "$BIN" -c tailwind.config.js -i app/static/css/input.css -o app/static/css/app.css --minify "$@"
