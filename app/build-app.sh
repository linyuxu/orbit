#!/bin/sh
# Build Orbit.app: compile the Tauri shell, wrap it in a macOS bundle, sign it ad hoc, install it.
#   app/build-app.sh            → dist/Orbit.app, copied to ~/Applications/Orbit.app
#   app/build-app.sh --no-install
# The app runs the server from this checkout (the repository it was built in), so UI and Python edits
# need no rebuild: ⌘⇧R reloads the page, View → Restart Server reloads the Python code.
set -eu
APP_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$APP_DIR/.." && pwd)
OUT="$ROOT/dist/Orbit.app"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$APP_DIR/Cargo.toml" | head -1)

[ -f "$APP_DIR/icons/Orbit.icns" ] || (cd "$ROOT" && uv run python app/make_icon.py)
(cd "$APP_DIR" && cargo build --release)

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$APP_DIR/target/release/orbit-app" "$OUT/Contents/MacOS/Orbit"
cp "$APP_DIR/icons/Orbit.icns" "$OUT/Contents/Resources/Orbit.icns"
cat > "$OUT/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Orbit</string>
  <key>CFBundleDisplayName</key><string>Orbit</string>
  <key>CFBundleIdentifier</key><string>dev.orbit.desk</string>
  <key>CFBundleExecutable</key><string>Orbit</string>
  <key>CFBundleIconFile</key><string>Orbit</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHumanReadableCopyright</key><string>Orbit — an IDE whose documents are coding-agent sessions</string>
  <key>NSCalendarsUsageDescription</key><string>Orbit shows your meetings (read-only) when Calendar is turned on in its Settings.</string>
  <key>NSCalendarsFullAccessUsageDescription</key><string>Orbit shows your meetings (read-only) when Calendar is turned on in its Settings. It never changes your calendars.</string>
</dict>
</plist>
EOF
codesign --force --deep --sign - "$OUT" >/dev/null 2>&1 || echo "warning: ad-hoc signing failed" >&2
echo "built $OUT"

if [ "${1:-}" != "--no-install" ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Orbit.app"
  cp -R "$OUT" "$HOME/Applications/Orbit.app"
  echo "installed $HOME/Applications/Orbit.app"
fi
