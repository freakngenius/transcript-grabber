#!/bin/zsh
# Build Transcript Grabber.app and install it in /Applications (override with DEST_DIR).
# Run again after editing app.py, index.html or anything in mac/.
set -euo pipefail
cd "${0:A:h}"

APP="Transcript Grabber"
DEST_DIR="${DEST_DIR:-/Applications}"
# Build outside Dropbox: its extended attributes break code signatures.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/transcript-grabber-build.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
BUNDLE="$WORK/$APP.app"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

echo "Compiling…"
swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macos13.0 mac/main.swift mac/ReelCollector.swift -o "$BUNDLE/Contents/MacOS/$APP"

echo "Drawing the icon…"
swift mac/make_icon.swift "$WORK/icon.png"
ICONSET="$WORK/AppIcon.iconset"
mkdir "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}.png" > /dev/null
  sips -z $((size * 2)) $((size * 2)) "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/AppIcon.icns"

cp mac/Info.plist "$BUNDLE/Contents/Info.plist"
cp app.py index.html "$BUNDLE/Contents/Resources/"
codesign --force --sign - "$BUNDLE"

# Quit a running copy first; its server exits when the app does.
if pgrep -x "$APP" > /dev/null; then
  pkill -x "$APP" || true
  sleep 1
fi
rm -rf "$DEST_DIR/$APP.app"
ditto "$BUNDLE" "$DEST_DIR/$APP.app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST_DIR/$APP.app"
echo "Installed $DEST_DIR/$APP.app"
