#!/usr/bin/env bash
#
# Runs the app's pure-logic tests (parser, tree matching, quiz, progress file)
# on the desktop JVM — no phone, no emulator.
#
#   ./android/test.sh [path/to/medo.medo]
#
# Build the bundle first with:  python -m medo.cli build-bank -o dist/medo.medo
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
JDK="$ROOT/tools/jdk8"
ZXING="$HERE/libs/zxing-core.jar"
OUT="$ROOT/build/jvmtest"
BUNDLE="${1:-$ROOT/dist/medo.medo}"

export JAVA_HOME="$(cygpath -w "$JDK")"
export PATH="$JDK/bin:$PATH"

if [ ! -f "$BUNDLE" ]; then
    echo "[!] bundle not found: $BUNDLE"
    echo "    python -m medo.cli build-bank -o dist/medo.medo"
    exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT"
javac -encoding UTF-8 -nowarn -cp "$(cygpath -w "$ZXING")" -d "$OUT" \
    "$HERE/src/ir/medo/app/MiniJson.java" \
    "$HERE/src/ir/medo/app/Question.java" \
    "$HERE/src/ir/medo/app/Node.java" \
    "$HERE/src/ir/medo/app/Bank.java" \
    "$HERE/src/ir/medo/app/Quiz.java" \
    "$HERE/src/ir/medo/app/ScanTarget.java" \
    "$HERE/src/ir/medo/app/QrDecoder.java" \
    "$HERE/src/ir/medo/app/StateStore.java" \
    "$HERE/src/ir/medo/app/Io.java" \
    "$HERE/tests/TestMain.java"
java -cp "$(cygpath -w "$OUT");$(cygpath -w "$ZXING")" ir.medo.app.TestMain "$BUNDLE"
