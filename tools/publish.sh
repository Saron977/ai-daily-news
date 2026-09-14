#!/usr/bin/env bash
# 一条命令完成当日归档：生成报告 -> 提交 -> 推送
#
#   tools/publish.sh              # 用今天日期
#   tools/publish.sh 2026-09-14   # 指定日期
#   VERSION=v0.2.0 tools/publish.sh   # 覆盖版本号
#
# 提交描述遵循 Dreame 固定格式：
#   【项目 版本号】【业务需求名】简短标题 - 改动点：具体改动说明
#
# 两个刻意的设计：
#   1. 描述按「本次实际改了什么」生成（新增/更新/重建），不写与实际不符的套话。
#   2. 渲染会把生成时刻写进产物，重复渲染必然产生时间戳 diff；
#      若 diff 去掉时间戳后为空，视为无实质变更，直接跳过提交，避免噪声提交。
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
  echo "  工作区干净，无需提交"
  exit 0
fi

# 判断是否只有「生成时间戳」在变（渲染必然刷新 BUILD 常量与页脚时间）
MEANINGFUL="$(git diff --cached -U0 \
  | grep -E '^[+-]' \
  | grep -vE '^(\+\+\+|---)' \
  | grep -vE 'const BUILD = |由 newspipe 生成|newspipe 生成 · ' || true)"
if [ -z "$MEANINGFUL" ]; then
  echo "  仅有生成时间戳变化，无实质内容变更，还原产物并跳过提交"
  git reset -q          # 取消暂存；新增文件退回未跟踪，不会被删除
  git checkout -- .     # 仅还原已跟踪文件（时间戳），未跟踪文件不受影响
  exit 0
fi

# ---------- 按实际变更拼装提交描述 ----------
read -r TOTAL SECTIONS < <(python3 - "$DATA" <<'PY'
import json,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
print(sum(len(s.get("items",[])) for s in d.get("sections",[])), len(d.get("sections",[])))
PY
)

# 数据文件是新增还是修改？决定标题用「归档」「更新」还是「重建」
DATA_STATUS="$(git diff --cached --name-status -- "$DATA" | cut -f1)"
case "$DATA_STATUS" in
  A) HEAD_WORD="归档"; DATA_WORD="新增 ${DATA} 内容源与 data/raw 检索留档" ;;
  M) HEAD_WORD="更新"; DATA_WORD="更新 ${DATA} 内容源" ;;
  *) HEAD_WORD="重建"; DATA_WORD="内容源未变，仅重建产物" ;;
esac

# 实际改动的文件清单（排除数据文件本身，避免与上面重复）
CHANGED="$(git diff --cached --name-only | grep -v "^${DATA}$" | paste -sd '、' - || true)"
CHANGED_DESC=""
[ -n "$CHANGED" ] && CHANGED_DESC="；变更文件：${CHANGED}"

MSG="【DailyNews ${VERSION}】【每日新闻】${HEAD_WORD} ${DATE} 晨报（${TOTAL} 条 / ${SECTIONS} 板块）"
MSG="${MSG} - 改动点：${DATA_WORD}；新鲜度审计通过（焦点板块 ≤3 天、其余 ≤7 天），产物自检通过（序号连续、total 一致、相对日期正确）"
MSG="${MSG}${CHANGED_DESC}"
LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || echo '')"
[ -n "$LAST_TAG" ] && MSG="${MSG}；基线 ${LAST_TAG}"

git commit -q -m "$MSG"
echo "  ✓ $(git log --oneline -1 | cut -c1-120)…"

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

SLUG="$(git remote get-url origin | sed -E 's#(git@[^:]+:|https://[^/]+/)##; s#\.git$##')"
git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1 || git branch --set-upstream-to="origin/$BRANCH" "$BRANCH" >/dev/null 2>&1 || true

echo
echo "✓ 已归档并推送：https://github.com/${SLUG}/blob/${BRANCH}/${REPORT}"
