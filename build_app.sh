#!/bin/zsh
# Builds "Parakeet Dictate.app": a native launcher that embeds this project's venv Python.
# Note: rebuilding changes the code signature, so macOS will ask for permissions again.
set -e
DIR="${0:A:h}"
APP="$DIR/Parakeet Dictate.app"
PYHOME="$(grep '^home' "$DIR/.venv/pyvenv.cfg" | cut -d' ' -f3)/.."
PYHOME="${PYHOME:A}"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Parakeet Dictate</string>
  <key>CFBundleDisplayName</key><string>Parakeet Dictate</string>
  <key>CFBundleIdentifier</key><string>local.parakeet-dictate</string>
  <key>CFBundleExecutable</key><string>ParakeetDictate</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.1</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Parakeet Dictate records while you hold Fn so it can transcribe your speech locally.</string>
</dict></plist>
PLIST
clang -O2 -o "$APP/Contents/MacOS/ParakeetDictate" "$DIR/launcher.c" \
  -I"$PYHOME/include/python3.11" -L"$PYHOME/lib" -lpython3.11 -Wl,-rpath,"$PYHOME/lib" \
  -DVENV_PYTHON="\"$DIR/.venv/bin/python3\"" -DSCRIPT="\"$DIR/dictate.py\""
codesign --force --sign - --identifier local.parakeet-dictate "$APP"
echo "Built: $APP"
