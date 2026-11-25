#!/bin/sh
# Build Orbit.app: compile the Tauri shell, wrap it in a macOS bundle, sign it ad hoc, install it.
#   app/build-app.sh            → dist/Orbit.app, copied to ~/Applications/Orbit.app
#   app/build-app.sh --no-install
#   app/build-app.sh --name "Orbit Lab" --port 8780 --state ~/.orbit-lab
#                               → a variant beside the main app: its own name, bundle id, port and folder
#                                 (dist/Orbit Lab.app, ~/Applications/Orbit Lab.app)
# The app runs the server from this checkout (the repository it was built in), so UI and Python edits
# need no rebuild: ⌘⇧R reloads the page, View → Restart Server reloads the Python code.
#
# A variant's Info.plist gives it ORBIT_APP_NAME, ORBIT_APP_PORT, ORBIT_APP_HOME (--state, or --home) and
# ORBIT_APP_STATE_DIR (LSEnvironment: however it is opened). They win over ORBIT_PORT / ORBIT_HOME / ORBIT_STATE_DIR
# (a terminal inside another Orbit exports those), and its server gets them as ORBIT_HOME / ORBIT_STATE_DIR: its
# server, state, data, logs and web storage (its own bundle id) are its own. --home DIR
# alone puts state and data under DIR; --state DIR is ORBIT_HOME and ORBIT_STATE_DIR both, unless --home is given.
set -eu
APP_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$APP_DIR/.." && pwd)
NAME=Orbit PORT="" STATE="" HOMEDIR="" INSTALL=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-install) INSTALL=0 ;;
    --name) NAME=${2:?--name needs a value}; shift ;;
    --port) PORT=${2:?--port needs a value}; shift ;;
    --state) STATE=${2:?--state needs a folder}; shift ;;
    --home) HOMEDIR=${2:?--home needs a folder}; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "build-app.sh: unknown argument $1 (--name N --port P --state DIR --home DIR --no-install)" >&2; exit 2 ;;
  esac
  shift
done
case "$PORT" in ""|*[!0-9]*) [ -z "$PORT" ] || { echo "build-app.sh: --port takes a number" >&2; exit 2; } ;; esac
case "$NAME" in *[/\"\\\'\&\<\>]*|"") echo "build-app.sh: --name: no / & < > or quotes" >&2; exit 2 ;; esac
case "$STATE$HOMEDIR" in *[\"\\\'\&\<\>]*) echo "build-app.sh: --state / --home: no & < > or quotes" >&2; exit 2 ;; esac
expand() { case "$1" in "~"|"~/"*) printf '%s' "$HOME${1#\~}" ;; *) printf '%s' "$1" ;; esac; }
STATE=$(expand "$STATE"); HOMEDIR=$(expand "$HOMEDIR")
[ -n "$STATE" ] && [ -z "$HOMEDIR" ] && HOMEDIR=$STATE
VARIANT=0
[ "$NAME" != Orbit ] || [ -n "$PORT" ] || [ -n "$STATE" ] || [ -n "$HOMEDIR" ] && VARIANT=1
if [ "$VARIANT" = 1 ] && [ "$NAME" = Orbit ]; then
  echo "build-app.sh: a variant needs its own --name (it must not replace ~/Applications/Orbit.app)" >&2; exit 2
fi
SLUG=$(printf '%s' "$NAME" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')
BUNDLE_ID=dev.orbit.desk
[ "$VARIANT" = 1 ] && BUNDLE_ID="dev.orbit.desk.$SLUG"
OUT="$ROOT/dist/$NAME.app"
ENVDICT=""  # a variant's settings: LSEnvironment, which macOS gives the app when it is opened (Finder, Dock, open)
if [ "$VARIANT" = 1 ]; then
  ENVDICT="  <key>LSEnvironment</key><dict>
    <key>ORBIT_APP_NAME</key><string>$NAME</string>"
  [ -n "$PORT" ] && ENVDICT="$ENVDICT
    <key>ORBIT_APP_PORT</key><string>$PORT</string>"
  [ -n "$HOMEDIR" ] && ENVDICT="$ENVDICT
    <key>ORBIT_APP_HOME</key><string>$HOMEDIR</string>"
  [ -n "$STATE" ] && ENVDICT="$ENVDICT
    <key>ORBIT_APP_STATE_DIR</key><string>$STATE</string>"
  ENVDICT="$ENVDICT
  </dict>"
fi
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$APP_DIR/Cargo.toml" | head -1)

[ -f "$APP_DIR/icons/Orbit.icns" ] || (cd "$ROOT" && uv run python app/make_icon.py)
(cd "$APP_DIR" && cargo build --release)

rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$APP_DIR/target/release/orbit-app" "$OUT/Contents/MacOS/$NAME"
cp "$APP_DIR/icons/Orbit.icns" "$OUT/Contents/Resources/Orbit.icns"
cat > "$OUT/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIconFile</key><string>Orbit</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
$ENVDICT
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHumanReadableCopyright</key><string>Orbit — an IDE whose documents are coding-agent sessions</string>
  <key>NSMicrophoneUsageDescription</key><string>Orbit listens while you talk to your agents by voice (GPT Live), only during a call you start.</string>
  <key>NSCalendarsUsageDescription</key><string>Orbit shows your meetings (read-only) when Calendar is turned on in its Settings.</string>
  <key>NSCalendarsFullAccessUsageDescription</key><string>Orbit shows your meetings (read-only) when Calendar is turned on in its Settings. It never changes your calendars.</string>
</dict>
</plist>
EOF
codesign --force --deep --sign - "$OUT" >/dev/null 2>&1 || echo "warning: ad-hoc signing failed" >&2
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
register() { [ -x "$LSREG" ] && "$LSREG" -f "$1" >/dev/null 2>&1 || true; }  # (macOS reads LSEnvironment anew)
register "$OUT"
echo "built $OUT"

if [ "$INSTALL" = 1 ]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/$NAME.app"
  cp -R "$OUT" "$HOME/Applications/$NAME.app"
  register "$HOME/Applications/$NAME.app"
  echo "installed $HOME/Applications/$NAME.app"
fi
