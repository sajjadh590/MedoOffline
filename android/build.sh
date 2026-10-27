#!/usr/bin/env bash
#
# Build medo.apk with the portable toolchain in tools/.
#
#   tools/jdk8                      javac, keytool, jar
#   tools/sdk/build-tools/30.0.3    aapt2, d8, zipalign, apksigner
#   tools/sdk/platforms/android-30  android.jar
#
# No Gradle, no Android Studio, no network. Output: dist/medo.apk
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
JDK="$ROOT/tools/jdk8"
SDK="$ROOT/tools/sdk"
BT="$SDK/build-tools/30.0.3"
ANDROID_JAR="$SDK/platforms/android-30/android.jar"
BUILD="$ROOT/build/android"
OUT="$ROOT/dist"
KEYSTORE="$HERE/medo-debug.keystore"
ZXING="$HERE/libs/zxing-core.jar"

win() { cygpath -w "$1"; }

for needed in "$JDK/bin/javac.exe" "$BT/aapt2.exe" "$BT/d8.bat" "$ANDROID_JAR" "$ZXING"; do
    if [ ! -e "$needed" ]; then
        echo "[!] missing $needed"
        echo "    run: medo share --help   (or reinstall the tools/ toolchain)"
        exit 1
    fi
done

export JAVA_HOME="$(win "$JDK")"
export PATH="$JDK/bin:$PATH"

rm -rf "$BUILD"
mkdir -p "$BUILD/res" "$BUILD/classes" "$BUILD/dex" "$OUT"

echo "[1/6] compiling resources (aapt2)"
"$BT/aapt2.exe" compile --dir "$HERE/res" -o "$BUILD/res.zip"
"$BT/aapt2.exe" link \
    -o "$BUILD/base.apk" \
    -I "$(win "$ANDROID_JAR")" \
    --manifest "$(win "$HERE/AndroidManifest.xml")" \
    --min-sdk-version 21 --target-sdk-version 30 \
    --version-code 1 --version-name 1.0 \
    "$BUILD/res.zip"

echo "[2/6] compiling java (javac)"
find "$HERE/src" -name '*.java' | while IFS= read -r f; do cygpath -w "$f"; done > "$BUILD/sources.txt"
javac -source 8 -target 8 \
    -bootclasspath "$(win "$ANDROID_JAR")" \
    -cp "$(win "$ZXING")" \
    -encoding UTF-8 -nowarn \
    -d "$BUILD/classes" \
    @"$(win "$BUILD/sources.txt")"

echo "[3/6] dexing (d8)"
jar cf "$BUILD/classes.jar" -C "$BUILD/classes" .
"$BT/d8.bat" --release --min-api 21 \
    --lib "$(win "$ANDROID_JAR")" \
    --output "$(win "$BUILD/dex")" \
    "$(win "$BUILD/classes.jar")" \
    "$(win "$ZXING")"

echo "[4/6] packaging"
python - "$BUILD" <<'PY'
import os
import shutil
import sys
import zipfile

build = sys.argv[1]
signed_input = os.path.join(build, "unsigned.apk")
shutil.copyfile(os.path.join(build, "base.apk"), signed_input)
with zipfile.ZipFile(signed_input, "a", zipfile.ZIP_DEFLATED) as apk:
    apk.write(os.path.join(build, "dex", "classes.dex"), "classes.dex")
print("      packed %.1f KB" % (os.path.getsize(signed_input) / 1024.0))
PY

echo "[5/6] aligning + signing"
if [ ! -f "$KEYSTORE" ]; then
    keytool -genkeypair -keystore "$KEYSTORE" -storepass android -keypass android \
        -alias medo -keyalg RSA -keysize 2048 -validity 10000 \
        -dname "CN=Medo Offline, O=Medo, C=IR" >/dev/null 2>&1
    echo "      created $KEYSTORE (debug key — keep it to ship updates)"
fi
"$BT/zipalign.exe" -f 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"
"$BT/apksigner.bat" sign \
    --ks "$(win "$KEYSTORE")" --ks-pass pass:android --key-pass pass:android \
    --out "$(win "$OUT/medo.apk")" \
    "$(win "$BUILD/aligned.apk")"

echo "[6/6] verifying"
"$BT/apksigner.bat" verify --print-certs "$OUT/medo.apk" | head -3
"$BT/aapt2.exe" dump badging "$OUT/medo.apk" | grep -E "^(package|sdkVersion|targetSdkVersion|application-label|launchable-activity)" || true

SIZE=$(stat -c %s "$OUT/medo.apk")
echo
echo "[+] dist/medo.apk  ($((SIZE / 1024)) KB)"
echo "    install: tools/sdk/platform-tools/adb.exe install -r dist/medo.apk"
