#!/usr/bin/env bash
# 一条命令跑完当日流程：拼装 -> 校验+渲染+索引 -> 产物自检
#
#   tools/daily.sh              # 用今天日期
#   tools/daily.sh 2026-09-14   # 指定日期
#   REASSEMBLE=1 tools/daily.sh # 强制从 data/raw 重建当日数据文件
#
# 前置：把检索产出放进 data/raw/（ai_coding.json / embodied.json /
#       weekly_tibo.json / companies.json / highlights.json）
#
# 注意：默认**不会**覆盖已存在的 data/<date>.json。
# 原因是拼装会无条件以 data/raw 为准重写当日数据，而人工审校后常直接改
# data/<date>.json（去重、改标题、换来源）。若每次都重拼，这些修正会被静默
# 回滚——2026-09-29 期就真的踩过一次：研究员在审校后重写了 raw，
# publish 重拼时把已改好的标题覆盖回旧版并发布了出去。
set -euo pipefail

cd "$(dirname "$0")/.."
DATE="${1:-$(date +%F)}"
OUT="AI新闻速览_${DATE}.html"
DATA="data/${DATE}.json"

echo "▶ 日期：$DATE"

if [ -f "$DATA" ] && [ -z "${REASSEMBLE:-}" ]; then
  echo "▶ 1/3 跳过拼装（$DATA 已存在，保护已有的人工审校；如需重建设 REASSEMBLE=1）"
elif [ -d data/raw ] && [ -n "$(ls -A data/raw 2>/dev/null)" ]; then
  echo "▶ 1/3 拼装 data/raw -> $DATA"
  python3 tools/newspipe.py assemble --date "$DATE" --force
else
  echo "▶ 1/3 跳过拼装（data/raw 为空，直接使用已有的 $DATA）"
fi

echo "▶ 2/3 校验 + 渲染 + 重建索引"
python3 tools/newspipe.py all --date "$DATE"

echo "▶ 3/3 产物自检"
node tools/selftest.js "$OUT"

echo
echo "✓ 完成：$(pwd)/${OUT}"
echo "  归档索引：$(pwd)/index.html"
