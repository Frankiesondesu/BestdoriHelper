#!/usr/bin/env bash
# 本地构建安卓 APK。
#
# 需要三样东西：
#   1. JDK 17 或 21（**不能用 JDK 25** —— Gradle 8.11 和 AGP 8.7 都不支持）
#   2. Android SDK：platforms;android-35 + build-tools;35.0.0
#   3. Gradle 8.9+（本项目用 8.11.1）
#
# 脚本会先找 `.workbuddy-ai/binaries` 下预装的那套（jdk21 / android-sdk /
# gradle-8.11.1），找不到就退回环境变量 JAVA_HOME / ANDROID_HOME / gradle。
#
# 用法：
#   bash scripts/build_apk.sh          # 构建 debug APK
#   bash scripts/build_apk.sh sync     # 只把网页资源同步进安卓工程
#
# 产物：android/app/build/outputs/apk/debug/app-debug.apk

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.workbuddy-ai/binaries"

# ---- 找工具链 ---------------------------------------------------------

find_jdk() {
  for c in "$BIN/jdk21/jdk" "$BIN/jdk17/jdk"; do
    if [ -x "$c/bin/java" ] || [ -x "$c/bin/java.exe" ]; then
      echo "$c"; return
    fi
  done
  if [ -n "${JAVA_HOME:-}" ]; then echo "$JAVA_HOME"; return; fi
  echo ""
}

find_sdk() {
  for c in "$BIN/android-sdk" "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}"; do
    if [ -n "$c" ] && [ -d "$c/platforms" ]; then
      echo "$c"; return
    fi
  done
  echo ""
}

find_gradle() {
  if [ -x "$BIN/gradle/gradle-8.11.1/bin/gradle" ]; then
    echo "$BIN/gradle/gradle-8.11.1/bin/gradle"; return
  fi
  command -v gradle 2>/dev/null || echo ""
}

JDK="$(find_jdk)"
SDK="$(find_sdk)"
GRADLE="$(find_gradle)"

if [ -z "$JDK" ]; then
  echo "✗ 找不到 JDK 17/21。装一个，或设 JAVA_HOME。" >&2
  echo "  （注意：JDK 25 对 Gradle 8.11 / AGP 8.7 太新，用不了）" >&2
  exit 1
fi
if [ -z "$SDK" ]; then
  echo "✗ 找不到 Android SDK（需要含 platforms 目录）。" >&2
  echo "  装法：sdkmanager \"platforms;android-35\" \"build-tools;35.0.0\" \"platform-tools\"" >&2
  exit 1
fi
if [ -z "$GRADLE" ]; then
  echo "✗ 找不到 Gradle 8.9+。" >&2
  exit 1
fi

# Git Bash 的路径是 /c/Users/... 这种 MSYS 形式，而 Gradle / Java 是 Windows
# 原生程序，**不认识它** —— 直接 export 等于没设，Gradle 会报
# "SDK location not found"。必须先转成 C:/Users/... 形式。
if command -v cygpath >/dev/null 2>&1; then
  JDK_WIN="$(cygpath -m "$JDK")"
  SDK_WIN="$(cygpath -m "$SDK")"
else
  JDK_WIN="$JDK"; SDK_WIN="$SDK"
fi
export JAVA_HOME="$JDK_WIN"
export ANDROID_HOME="$SDK_WIN"
export ANDROID_SDK_ROOT="$SDK_WIN"

# 再写一份 local.properties：Gradle 对它的优先级高于环境变量，最不容易出岔子
if [ -d "$REPO/android" ]; then
  printf 'sdk.dir=%s\n' "$SDK_WIN" > "$REPO/android/local.properties"
fi

echo "JDK     : $JAVA_HOME"
echo "SDK     : $ANDROID_HOME"
echo "Gradle  : $GRADLE"
echo

# ---- 同步网页资源 -----------------------------------------------------

cd "$REPO"
if [ -d node_modules/@capacitor/cli ]; then
  echo "→ 同步网页资源进安卓工程（cap sync）"
  npx cap sync android
else
  echo "! 没装 npm 依赖，跳过 cap sync（先跑 npm install）"
fi

if [ "${1:-}" = "sync" ]; then
  echo "只做同步，结束。"
  exit 0
fi

# ---- 构建 -------------------------------------------------------------

cd "$REPO/android"
echo
echo "→ 构建 debug APK"
"$GRADLE" assembleDebug --no-daemon

APK="$REPO/android/app/build/outputs/apk/debug/app-debug.apk"
if [ -f "$APK" ]; then
  echo
  echo "✓ 构建成功"
  ls -lh "$APK"
  echo
  echo "装到手机："
  echo "  1) 把 apk 传到手机（或用 adb install -r \"$APK\"）"
  echo "  2) 手机上允许「安装未知来源的应用」"
else
  echo "✗ 没找到产物：$APK" >&2
  exit 1
fi
