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

# ⚠️ 这个脚本**刻意不创建任何临时文件、也不调用 rm**。
#
# 2026-09-22 出过事故：当时用 TMP=".git/publish-tmp" + `rm -rf "$TMP"`，
# 一次 SIGTERM 之后 .git 被破坏（refs/ 和 pack 文件消失，77 MB 只剩 749 KB，
# git 直接报 "not a git repository"）。本机的 safe-delete 垫片有路径规范化 bug
# （实测会把 CWD 和绝对路径拼在一起，报 CanonicalizePath 错误），
# 在 .git 里做删除极不可控。
#
# 所以现在：下载的文件直接走**进程替换**比对，推送输出存进**变量**，
# 全程不落地、不删除。工作区文件也没丢过，但没必要冒这个险。

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

# 远端分支当前指向。走代理时 ls-remote 也会偶发断连（返回空），
# 一次失败不代表远端有问题，重试几次再下结论。
remote_sha() {
  local sha="" i
  for i in 1 2 3 4; do
    sha="$(git ls-remote origin "refs/heads/$BRANCH" 2>/dev/null | cut -f1)"
    if [ -n "$sha" ]; then printf '%s' "$sha"; return 0; fi
    [ "$i" -lt 4 ] && sleep 3
  done
  return 1
}

# 挑一个真能连上 GitHub 的代理。
#
# 环境里默认带着沙箱自己的代理（$http_proxy），它会挂 —— 实测表现是
# `CONNECT tunnel failed, response 502`，或者干脆连接超时（HTTP 000）。
# 这时退回 Frankieson 本机的 127.0.0.1:7897。
#
# ⚠️ 探针必须用「真实输出文件 + HTTP 状态码」，不能靠 curl 的退出码：
#   * `-o /dev/null` 在 Git Bash 下会写失败，curl 退出码 23，把能用的代理误判成不通
#   * `-f` 也会因 502 直接非零退出，信息量不如状态码
# 输出 "none" 表示直连可用；返回 1 表示都不通。
probe_url() {   # $1 = 代理（空串表示直连）
  local code
  # 只需要状态码，不要响应体；/dev/null 在 Git Bash 下写会失败（curl 退出码 23），
  # 但那不影响打印出来的状态码 —— 所以这里只看 code，不看退出码。
  if [ -z "$1" ]; then
    code="$(timeout 20 curl -s -o /dev/null -w '%{http_code}' \
              --noproxy '*' https://github.com 2>/dev/null)"
  else
    code="$(timeout 15 curl -s -o /dev/null -w '%{http_code}' \
              -x "$1" https://github.com 2>/dev/null)"
  fi
  [ "$code" = "200" ]
}

pick_proxy() {
  local p
  for p in "${http_proxy:-}" "http://127.0.0.1:7897"; do
    [ -n "$p" ] || continue
    if probe_url "$p"; then printf '%s' "$p"; return 0; fi
  done
  if probe_url ""; then printf 'none'; return 0; fi
  return 1
}

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
  # 必须检查退出码 —— 踩过：新建的 .git 没配 user.name/user.email 时
  # `git commit` 直接 fatal 失败，但脚本照样打印「已提交」，然后一路跑到
  # 「远端已是这个提交，无需推送」，看起来完全正常，其实什么都没提交。
  if ! git commit -q -F - <<<"$MSG"; then
    bad "提交失败"
    echo
    echo "  最常见的原因是这个仓库没配提交身份，设一下再试："
    echo "    git config user.name  \"你的名字\""
    echo "    git config user.email \"你的邮箱\""
    exit 1
  fi
  ok "已提交：$(git log --oneline -1)"
else
  ok "工作区干净"
fi

# ---- 2. 推送 -----------------------------------------------------------

step "2/4  推送到 origin/$BRANCH"

# 先把代理定下来，否则后面每一步都要跟超时和 502 纠缠
if ! PROXY="$(pick_proxy)"; then
  bad "沙箱代理、本机 7897、直连都不通，没法推送"
  exit 1
fi
case "$PROXY" in
  none)
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
    ok "直连可用" ;;
  "${http_proxy:-}")
    ok "用沙箱代理 $PROXY" ;;
  *)
    export http_proxy="$PROXY" https_proxy="$PROXY"
    ok "沙箱代理不通，改用 $PROXY" ;;
esac

LOCAL_SHA="$(git rev-parse HEAD)"
if ! REMOTE_SHA="$(remote_sha)"; then
  bad "连不上远端，或远端没有 $BRANCH 分支（重试 4 次都失败）"
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
  # 走代理时偶发 TLS 断连（schannel: server closed abruptly / missing close_notify），
  # 重试一次通常就好。重试前确认远端没被推上去，避免重复推。
  # 但**认证失败不重试** —— 那是凭据问题，重试多少次都一样，得让用户去重新授权。
  pushed=0
  for attempt in 1 2 3; do
    push_out="$(git push --progress origin "$BRANCH" 2>&1)"
    rc=$?
    printf '%s\n' "$push_out"
    if [ "$rc" -eq 0 ]; then
      pushed=1
      break
    fi

    if printf '%s' "$push_out" | grep -qiE "Authentication failed|Invalid username or token|could not read Username|terminal prompts disabled"; then
      bad "认证失败 —— 凭据无效或已被吊销（不是网络问题，重试没用）"
      echo
      echo "  恢复步骤："
      echo "    1) 清掉本机存的失效凭据："
      echo "       printf 'protocol=https\\nhost=github.com\\n\\n' | git credential reject"
      echo "    2) 触发浏览器重新授权（这步别设 GIT_TERMINAL_PROMPT=0，否则不弹窗）："
      echo "       git push --dry-run origin HEAD:refs/heads/__auth_probe"
      echo
      echo "  提示：'git ls-remote' 成功不代表凭据有效 —— 公开仓库匿名也能读。"
      echo "        要测凭据必须用 'git push --dry-run'。"
      exit 1
    fi

    now_remote="$(remote_sha || true)"
    if [ "$now_remote" = "$LOCAL_SHA" ]; then
      warn "推送报错，但远端已是目标提交（实际成功了）"
      pushed=1
      break
    fi
    if [ "$attempt" -lt 3 ]; then
      warn "第 $attempt 次推送失败（疑似网络），5 秒后重试…"
      sleep 5
    fi
  done
  if [ "$pushed" != "1" ]; then
    bad "推送失败（试了 3 次）"
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

# 关键两点：
#   1. 拿**仓库里的 blob** 当基准，不是工作区文件 —— core.autocrlf 会让两者差
#      「行数」个字节（app.js 1582 行 → 少 1582），拿工作区比会误判成没部署。
#   2. 用**进程替换**比对，不落任何临时文件（见文件开头的事故说明）。
deadline=$(( $(date +%s) + 480 ))
try=0
while :; do
  try=$((try + 1))
  all_same=1
  detail=""
  for f in app.js style.css; do
    if cmp -s <(git cat-file blob "HEAD:docs/$f" 2>/dev/null) \
              <(timeout 30 curl -sf "$SITE/$f?v=$RANDOM$try" 2>/dev/null); then
      detail="$detail $f=一致"
    else
      all_same=0
      detail="$detail $f=不一致或取不到"
    fi
  done

  if [ "$all_same" = "1" ]; then
    ok "线上与仓库一致：$detail"
    echo
    echo "  网页版：$SITE/"
    echo
    echo "  提醒：安装包（APK / Windows）不在 git 里，用户从 Releases 下载 ——"
    echo "        而 Release **只有打 tag 才会更新**（build-apps.yml 的 release 任务）。"
    echo "        所以改了 docs/ 或要发新版时，别忘了："
    echo "          1) 先改 package.json 的 version（安卓版本号的唯一来源）"
    echo "          2) git tag -a v1.x.0 -F -   然后  git push origin v1.x.0"
    echo "          3) 等「构建安装包」跑完（安卓几分钟，Windows 十几分钟）"
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
