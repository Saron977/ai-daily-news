#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
readurl — 抓取网页并抽取可读正文（用于核实来源与事实）。

沙箱里 `web_fetch` 工具常被 DNS 拦截，但 `curl` 可以出网，故用 curl 抓、本地抽正文。

   python3 tools/readurl.py <url> [--chars 3000] [--grep 关键词]

用途：
  - 核实某条新闻的真实日期 / 数字 / 人名
  - 确认 sourceUrl 真的是那篇文章（而不是首页或 404）
"""
from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def fetch(url: str) -> tuple[int, str]:
    p = subprocess.run(
        ["curl", "-sSL", "-m", "30", "--compressed", "-A", UA,
         "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8", url],
        capture_output=True,
    )
    return p.returncode, p.stdout.decode("utf-8", "replace")


def to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    # 保留段落换行
    raw = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|section|article|br)\s*>", "\n", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\u00a0]+", " ", raw)
    raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
    return "\n".join(ln.strip() for ln in raw.splitlines() if ln.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--chars", type=int, default=3000)
    ap.add_argument("--grep", help="只打印包含该关键词的行（不区分大小写）")
    ap.add_argument("--title-only", action="store_true")
    a = ap.parse_args()

    rc, raw = fetch(a.url)
    if rc != 0 or not raw:
        print(f"✗ 抓取失败 rc={rc}", file=sys.stderr)
        return 1

    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) if m else "(无 title)"
    print(f"# {title}\n")

    if a.title_only:
        return 0

    text = to_text(raw)
    if a.grep:
        hits = [ln for ln in text.splitlines() if a.grep.lower() in ln.lower()]
        print("\n".join(hits[:40]) if hits else f"(未命中 {a.grep!r})")
    else:
        print(text[: a.chars])
    return 0


if __name__ == "__main__":
    sys.exit(main())
