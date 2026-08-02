#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NATIVE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DERIVED_DIR="$NATIVE_DIR/build/DerivedData"
DIST_DIR="$NATIVE_DIR/dist"
STAGE_DIR="$NATIVE_DIR/build/dmg-stage"

cd "$NATIVE_DIR"

if ! command -v xcodegen >/dev/null 2>&1; then
  echo "xcodegen est requis (brew install xcodegen)." >&2
  exit 1
fi

xcodegen generate
xcodebuild \
  -project JarvisMac.xcodeproj \
  -scheme JarvisMac \
  -configuration Release \
  -derivedDataPath "$DERIVED_DIR" \
  CODE_SIGNING_ALLOWED=NO \
  build

mkdir -p "$DIST_DIR" "$STAGE_DIR"
rm -rf "$DIST_DIR/Jarvis.app" "$STAGE_DIR/Jarvis.app"
cp -R "$DERIVED_DIR/Build/Products/Release/Jarvis.app" "$DIST_DIR/Jarvis.app"

# Une signature ad hoc rend le prototype directement lançable localement.
codesign --force --deep --sign - "$DIST_DIR/Jarvis.app"
cp -R "$DIST_DIR/Jarvis.app" "$STAGE_DIR/Jarvis.app"
ln -sfn /Applications "$STAGE_DIR/Applications"

rm -f "$DIST_DIR/Jarvis-Prototype.dmg"
hdiutil create \
  -volname "Jarvis Prototype" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DIST_DIR/Jarvis-Prototype.dmg"

echo "App : $DIST_DIR/Jarvis.app"
echo "DMG : $DIST_DIR/Jarvis-Prototype.dmg"
