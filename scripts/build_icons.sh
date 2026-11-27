#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p assets/png
rsvg-convert -w 1024 -h 1024 assets/icon.svg -o assets/png/icon-1024.png
rsvg-convert -w 512 -h 512 assets/icon.svg -o assets/png/icon-512.png
rsvg-convert -w 256 -h 256 assets/icon.svg -o assets/png/icon-256.png
rsvg-convert -w 1320 -h 360 assets/logo.svg -o assets/png/logo.png
rsvg-convert -w 1024 -h 1024 assets/logo-mark.svg -o assets/png/logo-mark.png

rm -rf assets/AppIcon.iconset
mkdir -p assets/AppIcon.iconset
for s in 16 32 64 128 256 512 1024; do
  sips -z "$s" "$s" assets/png/icon-1024.png --out "assets/AppIcon.iconset/tmp-$s.png" >/dev/null
done
cp assets/AppIcon.iconset/tmp-16.png assets/AppIcon.iconset/icon_16x16.png
cp assets/AppIcon.iconset/tmp-32.png assets/AppIcon.iconset/icon_16x16@2x.png
cp assets/AppIcon.iconset/tmp-32.png assets/AppIcon.iconset/icon_32x32.png
cp assets/AppIcon.iconset/tmp-64.png assets/AppIcon.iconset/icon_32x32@2x.png
cp assets/AppIcon.iconset/tmp-128.png assets/AppIcon.iconset/icon_128x128.png
cp assets/AppIcon.iconset/tmp-256.png assets/AppIcon.iconset/icon_128x128@2x.png
cp assets/AppIcon.iconset/tmp-256.png assets/AppIcon.iconset/icon_256x256.png
cp assets/AppIcon.iconset/tmp-512.png assets/AppIcon.iconset/icon_256x256@2x.png
cp assets/AppIcon.iconset/tmp-512.png assets/AppIcon.iconset/icon_512x512.png
cp assets/AppIcon.iconset/tmp-1024.png assets/AppIcon.iconset/icon_512x512@2x.png
rm assets/AppIcon.iconset/tmp-*.png
iconutil -c icns assets/AppIcon.iconset -o assets/AppIcon.icns
rm -rf assets/AppIcon.iconset
cp assets/AppIcon.icns desktop/AppIcon.icns

cd "$ROOT/desktop-tauri"
PATH="$HOME/.cargo/bin:$PATH" npx --yes @tauri-apps/cli@2 icon ../assets/png/icon-1024.png
rm -rf src-tauri/icons/android src-tauri/icons/ios
find src-tauri/icons -maxdepth 1 -type f \( -name 'Square*' -o -name 'StoreLogo.png' \) -delete
echo "icons updated"
