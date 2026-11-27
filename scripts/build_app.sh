#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
swift build -c release --package-path "$ROOT/desktop"
BIN_DIR="$(swift build -c release --package-path "$ROOT/desktop" --show-bin-path)"
APP="$ROOT/dist/Daythread.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN_DIR/Daythread" "$APP/Contents/MacOS/Daythread"
cp "$ROOT/desktop/Info.plist" "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources"
cp "$ROOT/desktop/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "$APP"
