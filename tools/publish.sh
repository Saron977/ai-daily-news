#!/usr/bin/env bash
# 一条命令完成当日归档：生成报告 -> 提交 -> 推送
#
#   tools/publish.sh              # 用今天日期
#   tools/publish.sh 2026-09-14   # 指定日期
#   VERSION=v0.2.0 tools/publish.sh   # 覆盖版本号
#
# 提交描述遵循 Dreame 固定格式：
#   【项目 版本号】【业务需求名】简短标题 - 改动点：具体改动说明
set -euo pipefail

cd "$(dirname "$0")/.."
DATE="${1:-$(date +%F)}"
VERSION="${VERSION:-v0.1.0}"
REPORT="AI新闻速览_${DATE}.html"
DATA="data/${DATE}.json"

command -v gh >/dev/null || { echo "✗ 未找到 gh CLI，请先 brew install gh && gh auth login"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "✗ gh 未登录，请先执行 gh auth login"; exit 1; }

if [ ! -f "$DATA" ]; then
  echo "✗ 找不到当日内容文件 $DATA"
  echo "  先准备 data/raw/ 下的检索产出，或执行：python3 tools/newspipe.py new --date $DATE"
  exit 1
fi

# ---------- 1/3 生成（校验不通过会在此中止） ----------
echo "▶ 1/3 生成报告"
tools/daily.sh "$DATE"

# ---------- 2/3 提交 ----------
echo
echo "▶ 2/3 提交"
git add -A

if git diff --cached --quiet; then
  echo "  没有需要提交的变更（报告内容与上次一致），跳过提交与推送"
  exit 0
fi

# 从数据文件里取条数与板块数，避免写死
read -r TOTAL SECTIONS < <(python3 - "$DATA" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
print(sum(len(s.get("items",[])) for s in d.get("sections",[])), len(d.get("sections",[])))
PY
)

LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo '')"
MSG="【DailyNews ${VERSION}】【每日新闻】归档 ${DATE} 晨报（${TOTAL} 条 / ${SECTIONS} 板块）"
MSG="${MSG} - 改动点：新增 ${DATA} 内容源与 data/raw 检索留档；渲染产出 ${REPORT} 并重建 index.html 与 feed.xml；"
MSG="${MSG}新鲜度审计通过（焦点板块 ≤3 天、其余 ≤7 天），产物自检通过（序号连续、total 一致、相对日期正确）"
[ -n "$LAST_TAG" ] && MSG="${MSG}；基线 ${LAST_TAG}"

git commit -q -m "$MSG"
echo "  ✓ $(git log --oneline -1)"

# ---------- 3/3 推送 ----------
echo
echo "▶ 3/3 推送"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if ! git push origin "$BRANCH" 2>/dev/null; then
  echo "  推送被拒，尝试 git pull --rebase 后重推"
  if git pull --rebase origin "$BRANCH"; then
    git push origin "$BRANCH"
  else
    echo "✗ rebase 出现冲突，已停下。请手动解决后重新推送（不做 force push）"
    exit 1
  fi
fi

REMOTE_URL="$(git remote get-url origin | sed -E 's#(git@[^:]+:|https://[^/]+/)##; s#\.git$##')"
# 分支无上游时补设跟踪
git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1 || git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null 2>&1 || true

echo
echo "✓ 已归档并推送：https://github.com/${REMOTE_URL}/blob/${BRANCH}/${REPORT}"
