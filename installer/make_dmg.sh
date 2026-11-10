#!/usr/bin/env bash
# Package dist/ALPACKA.app as ALPACKA-<version>-macos-arm64.dmg, then check
# that the app inside the finished image still has a valid signature.
#
# Usage: bash installer/make_dmg.sh <version>
# Run after `python build.py`. Needs create-dmg (brew install create-dmg).
#
# release.yml and CI both run this script, so the release's packaging is
# tested on every pull request rather than first on release day.
set -euo pipefail

version="${1:?usage: make_dmg.sh <version>}"
dmg="ALPACKA-${version}-macos-arm64.dmg"
app="dist/ALPACKA.app"

# The app is built for the runner's CPU, and the DMG's name says arm64
# (Apple silicon). Building on anything else would ship a mislabeled file.
if [ "$(uname -m)" != "arm64" ]; then
  echo "::error::Built on $(uname -m), but the DMG is named for arm64; update the name in installer/make_dmg.sh and release.yml" >&2
  exit 1
fi

if [ ! -d "$app" ]; then
  echo "::error::$app not found; run python build.py first" >&2
  ls -la dist/ >&2 || true
  exit 1
fi

rm -f "$dmg"
echo "Packaging $app -> $dmg"
# create-dmg occasionally exits non-zero after producing the file
# (AppleScript timing on CI). Tolerate that; the checks below catch a bad
# image.
create-dmg \
  --volname "ALPACKA ${version}" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "ALPACKA.app" 175 190 \
  --hide-extension "ALPACKA.app" \
  --app-drop-link 425 190 \
  "$dmg" \
  "$app" || true
test -f "$dmg" || { echo "::error::$dmg was not created" >&2; exit 1; }
echo "Wrote $dmg ($(stat -f%z "$dmg") bytes)"

# Check what users get: the app as it sits inside the image. macOS calls a
# downloaded app with a broken signature "damaged" and won't open it.
mnt="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$dmg" > /dev/null
trap 'hdiutil detach "$mnt" > /dev/null || true' EXIT
codesign --verify --deep --strict --verbose=2 "$mnt/ALPACKA.app"
