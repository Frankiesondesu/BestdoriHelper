#!/usr/bin/env bash
# 推送当前工作到 GitHub，并确认 GitHub Pages 上的网页版是最新的。
#
# 用法：
#   bash scripts/publish.sh                     # 只推送已提交的内容 + 校验 Pages
#   bash scripts/publish.sh -m "提交信息"         # 顺便把未提交的改动提交掉
#   bash scripts/publish.sh -m "..." --no-wait   # 不等 Pages 部署，推完就走
#
# 为什么要有这个脚本 —— 这几步各有一个坑，手工做很容易漏：
#
#   1. **必须设 GIT_TERMINAL_PROMPT=0**。凭据助手是本机的 GUI 选择器，
#      非交互会话里会一直等窗口，表现是 `git push` 几分钟没有任何输出然后被
#      超时杀掉，看起来像网络问题，其实是在等凭据。
#
#   2. **Pages 只在 docs/** 变动时才重新部署**（见 .github/workflows/pages.yml
#      的 paths 过滤）。只改了 src/ 或 scripts/ 的话，推送成功 ≠ 网页版更新，
#      脚本会把这一点明确报出来，免得误以为线上坏了。
#
#   3. **线上文件和本地文件字节数天然不同**。core.autocrlf=true 会把 CRLF
#      规范成 LF 再入库，每行少 1 字节（app.js 1582 行 → 少 1582 字节）。
#      所以校验要拿**仓库里的 blob** 跟线上比，不能拿工作区文件比，
#      否则会误判成「部署没生效」。
#
#   4. 推送体积可能不小（Photo/ 截图约 1.6 MB/张），慢是正常的 —— 实测
#      25 MiB 走了 10 分钟。别用短超时，也别以为卡死了。
#
# 需要网络，请在关闭沙箱的模式下运行。

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

BRANCH="${BRANCH:-master}"
SITE="https://frankiesondesu.github.io/BestdoriHelper"
WAIT=1
MSG=""

while [ $# -gt 0 ]; do
  case "$1" in
    -m|--message) MSG="${2:-}"; shift 2 ;;
    --no-wait)    WAIT=0; shift ;;
    -h|--help)    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

export GIT_TERMINAL_PROMPT=0

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

# ---- 1. 有改动就提交 ---------------------------------------------------

step "1/4  检查工作区"

if [ -n "$(git status --porcelain)" ]; then
  if [ -z "$MSG" ]; then
    bad "有未提交的改动，但没给提交信息"
    git -c core.quotepath=false status --short | head -20
    echo
    echo "  用 -m 给出提交信息，例如："
    echo "    bash scripts/publish.sh -m \"修 xxx\""
    exit 1
  fi
  git add -A
  git commit -q -F - <<<"$MSG"
  ok "已提交：$(git log --oneline -1)"
else
  ok "工作区干净"
fi

# ---- 2. 推送 -----------------------------------------------------------

step "2/4  推送到 origin/$BRANCH"

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | cut -f1)"

if [ -z "$REMOTE_SHA" ]; then
  bad "连不上远端，或远端没有 $BRANCH 分支"
  exit 1
fi

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
  ok "远端已经是这个提交，无需推送"
  PUSHED=0
else
  # 远端有本地没有的提交 -> 不是快进，交给人来判断，别自动合并
  if ! git merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA" 2>/dev/null; then
    bad "远端有本地没有的提交（$REMOTE_SHA），不是快进推送"
    echo "  先 git pull --rebase origin $BRANCH 再试"
    exit 1
  fi
  echo "  本地 $LOCAL_SHA -> 远端 $REMOTE_SHA，开始推送（体积大时可能要十几分钟）…"
  if ! git push --progress origin "$BRANCH"; then
    bad "推送失败"
    exit 1
  fi
  ok "已推送 $(git rev-parse --short HEAD)"
  PUSHED=1
fi

# ---- 3. 判断这次推送会不会触发 Pages -----------------------------------

step "3/4  判断 Pages 是否会被触发"

# 本次推送实际改了 docs/ 没有 —— Pages 的 paths 过滤只看这个
if [ "$PUSHED" = "1" ] && ! git diff --quiet "$REMOTE_SHA" "$LOCAL_SHA" -- docs/ 2>/dev/null; then
  CHANGED_DOCS=1
  ok "docs/ 有变动 —— Pages 会重新部署"
elif [ "$PUSHED" = "1" ]; then
  CHANGED_DOCS=0
  warn "本次没有改到 docs/ —— Pages 不会重新部署（网页版维持原样）"
  warn "这是设计如此（pages.yml 的 paths 过滤），网页版本来就该是旧的就不用管"
else
  # 没推送：线上应该已经是 HEAD，照样校验一遍
  CHANGED_DOCS=1
  ok "没有新提交，直接校验线上是否已与 HEAD 一致"
fi

if [ "$WAIT" = "0" ]; then
  echo
  echo "已跳过 Pages 校验（--no-wait）"
  exit 0
fi

# ---- 4. 等 Pages 部署完，并校验线上 = 仓库 ------------------------------

step "4/4  等待 Pages 部署并校验（最多 8 分钟）"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 关键：拿**仓库里的 blob** 当基准，不是工作区文件 ——
# core.autocrlf 会让两者差「行数」个字节，拿工作区比会误判成没部署。
for f in app.js style.css index.html; do
  git cat-file blob "HEAD:docs/$f" > "$TMP/repo_$f" 2>/dev/null || : > "$TMP/repo_$f"
done

deadline=$(( $(date +%s) + 480 ))
try=0
while :; do
  try=$((try + 1))
  all_same=1
  detail=""
  for f in app.js style.css; do
    if timeout 30 curl -sf -o "$TMP/live_$f" \
         "$SITE/$f?v=$RANDOM$try" 2>/dev/null; then
      if cmp -s "$TMP/repo_$f" "$TMP/live_$f"; then
        detail="$detail $f=一致"
      else
        all_same=0
        detail="$detail $f=旧($(stat -c %s "$TMP/live_$f" 2>/dev/null || echo '?')B)"
      fi
    else
      all_same=0
      detail="$detail $f=取不到"
    fi
  done

  if [ "$all_same" = "1" ]; then
    ok "线上与仓库一致：$detail"
    echo
    echo "  网页版：$SITE/"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    bad "等了 8 分钟线上仍不是最新：$detail"
    echo "  查一下部署工作流："
    echo "    https://github.com/Frankiesondesu/BestdoriHelper/actions/workflows/pages.yml"
    echo "  注意 GitHub Pages 有 CDN 缓存，偶尔会比工作流晚几分钟。"
    exit 1
  fi

  printf '  第 %d 次：%s，10 秒后重试…\n' "$try" "$detail"
  sleep 10
done
