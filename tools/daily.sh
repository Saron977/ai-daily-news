#!/usr/bin/env bash
# 一条命令跑完当日流程：拼装 -> 校验+渲染+索引 -> 产物自检
#
#   tools/daily.sh              # 用今天日期
#   tools/daily.sh 2026-09-14   # 指定日期
#
# 前置：把检索产出放进 data/raw/（ai_coding.json / embodied.json /
#       weekly_tibo.json / companies.json / highlights.json）
set -euo pipefail

cd "$(dirname "$0")/.."
DATE="${1:-$(date +%F)}"
OUT="AI新闻速览_${DATE}.html"

echo "▶ 日期：$DATE"

if [ -d data/raw ] && [ -n "$(ls -A data/raw 2>/dev/null)" ]; then
  echo "▶ 1/3 拼装 data/raw -> data/${DATE}.json"
  python3 tools/newspipe.py assemble --date "$DATE" --force
else
  echo "▶ 1/3 跳过拼装（data/raw 为空，直接使用已有的 data/${DATE}.json）"
fi

echo "▶ 2/3 校验 + 渲染 + 重建索引"
python3 tools/newspipe.py all --date "$DATE"

echo "▶ 3/3 产物自检"
node tools/selftest.js "$OUT"

echo
echo "✓ 完成：$(pwd)/${OUT}"
echo "  归档索引：$(pwd)/index.html"
