#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
newspipe — 每日 AI 新闻速览 生成/校验/索引工具链

设计要点（相对早期 WorkBuddy 手写 HTML 的改进）：
  1. 内容与渲染分离：内容放 data/YYYY-MM-DD.json，HTML 由模板渲染，不再手改 HTML。
  2. 全局序号自动生成（n），总条数自动统计，杜绝序号断号 / total 对不上。
  3. 新鲜度审计内置：焦点板块（focus=true）≤3 天、其余板块 ≤7 天，越界即报错。
  4. 深链 / 深色模式 / 搜索 / 打印样式 / RSS 索引。
  5. 零依赖（仅标准库），无硬编码绝对路径。

用法：
  python3 tools/newspipe.py assemble --date 2026-09-14     # data/raw/*.json -> data/2026-09-14.json
  python3 tools/newspipe.py all      --date 2026-09-14     # 校验 + 渲染 + 重建索引
  python3 tools/newspipe.py validate --date 2026-09-14
  python3 tools/newspipe.py render   --date 2026-09-14
  python3 tools/newspipe.py index
  python3 tools/newspipe.py check-links --date 2026-09-14
  python3 tools/newspipe.py new      --date 2026-09-15     # 从模板起一份空白数据
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
DATA_DIR = os.path.join(ROOT, "data")
TEMPLATE = os.path.join(TOOLS, "template.html")

REPORT_GLOB = re.compile(r"^AI新闻速览_(\d{4}-\d{2}-\d{2})\.html$")
DATA_RE = re.compile(r"^const DATA = (\{.*\});$", re.M)

FOCUS_MAX_DAYS = 3
GENERAL_MAX_DAYS = 7
BJ = dt.timezone(dt.timedelta(hours=8))

# 今天(报告日) 的内容期望的板块骨架；focus=True 的走 3 天窗口
DEFAULT_SKELETON = [
    ("AI Coding", True, "每日焦点 · 近 3 天"),
    ("具身智能", True, "每日焦点 · 近 3 天"),
    ("一周大事", False, "本周窗口"),
    ("Anthropic（A社）", False, "本周窗口"),
    ("OpenAI", False, "本周窗口"),
    ("Tibo · Codex 额度重置信号", False, "本周窗口"),
]

C_RED, C_YEL, C_GRN, C_DIM, C_OFF = "\033[31m", "\033[33m", "\033[32m", "\033[2m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C_RED = C_YEL = C_GRN = C_DIM = C_OFF = ""


# ---------------------------------------------------------------- helpers
def log(msg: str, color: str = "") -> None:
    print(f"{color}{msg}{C_OFF}")


def die(msg: str, code: int = 2):
    log(f"✗ {msg}", C_RED)
    sys.exit(code)


def data_path(date: str) -> str:
    return os.path.join(DATA_DIR, f"{date}.json")


def report_path(date: str) -> str:
    return os.path.join(ROOT, f"AI新闻速览_{date}.html")


def load_data(date: str) -> dict:
    p = data_path(date)
    if not os.path.exists(p):
        die(f"找不到数据文件：{p}\n  先跑 `newspipe.py new --date {date}` 起一份，或检查日期。")
    with open(p, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as e:
            die(f"{p} 不是合法 JSON：{e}")


def parse_iso(s: str):
    """宽松解析 ISO8601，返回带时区的 datetime 或 None。"""
    if not isinstance(s, str) or not s.strip():
        return None
    t = s.strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    return d.replace(tzinfo=BJ) if d.tzinfo is None else d


def age_days(published: str, report_date: str):
    """发布日相对报告日的天数（北京时间日历日）。0=当天, 1=昨天 ... 负数=未来。"""
    d = parse_iso(published)
    if d is None:
        return None
    ry, rm, rd = (int(x) for x in report_date.split("-"))
    pub = d.astimezone(BJ).date()
    return (dt.date(ry, rm, rd) - pub).days


def is_bare_homepage(url: str) -> bool:
    m = re.match(r"^https?://[^/]+/?$", url or "")
    return bool(m)


# ---------------------------------------------------------------- validate
def validate(data: dict, date: str, *, strict: bool = True) -> tuple[list, list]:
    """返回 (errors, warnings)。errors 非空即视为不可交付。"""
    errors: list[str] = []
    warnings: list[str] = []

    if data.get("date") != date:
        errors.append(f"data.date={data.get('date')!r} 与请求日期 {date!r} 不一致")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        errors.append("sections 缺失或为空")
        return errors, warnings

    seen_urls: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    total = 0
    focus_max = int(data.get("focusWindowDays", FOCUS_MAX_DAYS))
    general_max = int(data.get("generalWindowDays", GENERAL_MAX_DAYS))

    for si, sec in enumerate(sections):
        label = sec.get("label") or f"<section#{si}>"
        items = sec.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"[{label}] items 缺失或为空")
            continue
        limit = focus_max if sec.get("focus") else general_max
        kind = "焦点" if sec.get("focus") else "一般"

        for ii, it in enumerate(items):
            total += 1
            where = f"[{label} #{ii + 1}]"
            for key in ("title", "summary", "sourceName", "sourceUrl", "publishedAt"):
                if not it.get(key):
                    errors.append(f"{where} 缺字段 {key}")

            title = (it.get("title") or "").strip()
            url = (it.get("sourceUrl") or "").strip()
            pub = it.get("publishedAt")
            summ = (it.get("summary") or "").strip()

            if title:
                if len(title) > 70:
                    warnings.append(f"{where} 标题过长（{len(title)} 字）：{title[:24]}…")
                if title in seen_titles:
                    warnings.append(f"{where} 标题与「{seen_titles[title]}」重复：{title[:24]}…")
                seen_titles[title] = label

            if url:
                if not url.startswith(("http://", "https://")):
                    errors.append(f"{where} sourceUrl 非 http(s)：{url}")
                elif is_bare_homepage(url):
                    warnings.append(f"{where} sourceUrl 是首页而非具体文章：{url}")
                # 同一篇聚合稿覆盖多条新闻是常态（AI Coding Roundup / AGI HUNT Daily 等），
                # 因此重复链接只提示、不判错。
                if url in seen_urls:
                    warnings.append(f"{where} 与「{seen_urls[url]}」共用来源链接：{url}")
                seen_urls[url] = label

            if summ:
                if not re.search(r"【值得关注", summ):
                    warnings.append(f"{where} 摘要缺少【值得关注】解读句")
                if not (60 <= len(summ) <= 320):
                    warnings.append(f"{where} 摘要长度异常（{len(summ)} 字）")

            age = age_days(pub, date)
            if age is None:
                errors.append(f"{where} publishedAt 无法解析：{pub!r}")
            elif age < 0:
                warnings.append(f"{where} publishedAt 晚于报告日（{-age} 天后）：{pub}")
            elif age > limit:
                errors.append(
                    f"{where} 超出新鲜度窗口：{age} 天前 > {limit} 天（{kind}板块）· {title[:26]}…"
                )

    # 骨架对比（板块名/顺序/数量）
    want = [s[0] for s in DEFAULT_SKELETON]
    got = [s.get("label") for s in sections]
    if got != want:
        warnings.append(f"板块与默认骨架不一致：\n      期望 {want}\n      实际 {got}")

    if isinstance(data.get("total"), int) and data["total"] != total:
        errors.append(f"data.total={data['total']} 与实际条数 {total} 不一致（渲染时会以实际为准）")

    return errors, warnings


def cmd_validate(args) -> int:
    data = load_data(args.date)
    errors, warnings = validate(data, args.date)
    total = sum(len(s.get("items", [])) for s in data.get("sections", []))

    # 新鲜度概览
    log(f"\n📅 {args.date} · {total} 条 · {len(data.get('sections', []))} 板块")
    for sec in data.get("sections", []):
        ages = [age_days(i.get("publishedAt"), args.date) for i in sec.get("items", [])]
        ages = [a for a in ages if a is not None]
        limit = FOCUS_MAX_DAYS if sec.get("focus") else GENERAL_MAX_DAYS
        mx = max(ages) if ages else "-"
        flag = "✓" if isinstance(mx, int) and mx <= limit else "✗"
        tone = C_GRN if flag == "✓" else C_RED
        log(f"  {flag} {sec.get('label','?'):<26} {len(ages)} 条  最旧 {mx} 天 / 上限 {limit} 天", tone)

    for w in warnings:
        log(f"  ⚠ {w}", C_YEL)
    for e in errors:
        log(f"  ✗ {e}", C_RED)
    log("")
    if errors:
        log(f"校验失败：{len(errors)} 个错误，{len(warnings)} 个警告", C_RED)
        return 1
    log(f"校验通过：0 错误，{len(warnings)} 个警告", C_GRN)
    return 0


# ---------------------------------------------------------------- render
def normalize(data: dict, date: str) -> dict:
    """渲染前归一化：全局重排序号 n、重算 total、补齐默认字段。"""
    n = 0
    for sec in data.get("sections", []):
        for it in sec.get("items", []):
            n += 1
            it["n"] = n
            it.setdefault("summaryFull", it.get("summary", ""))
    data["total"] = n
    data["date"] = date
    data.setdefault("source", "AI HOT")
    data.setdefault("reportName", "AI 晨报")
    data.setdefault("eyebrow", "AI HOT · 每日晨报")
    data.setdefault("focusNote", "侧重方向：AI Coding 与 具身智能")
    return data


def cmd_render(args) -> int:
    data = load_data(args.date)
    errors, _ = validate(data, args.date)
    fatal = [e for e in errors if "total" not in e]  # total 会被重算，不算致命
    if fatal and not args.force:
        for e in fatal:
            log(f"  ✗ {e}", C_RED)
        die(f"校验未通过，已中止渲染（加 --force 可强行渲染）")

    data = normalize(data, args.date)

    if not os.path.exists(TEMPLATE):
        die(f"模板不存在：{TEMPLATE}")
    with open(TEMPLATE, encoding="utf-8") as fh:
        tpl = fh.read()

    title = f"{args.date} {data['reportName']} · {data['total']} 条"
    desc = "；".join(data.get("highlights", [])[:2]) or f"{args.date} AI 行业动态 {data['total']} 条"
    build = dt.datetime.now(BJ).strftime("%Y-%m-%d %H:%M")

    out = (tpl
           .replace("__TITLE__", html.escape(title, quote=True))
           .replace("__DESC__", html.escape(desc[:180], quote=True))
           .replace("__BUILD__", json.dumps(build, ensure_ascii=False))
           .replace("__DATA__", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))

    dst = args.out or report_path(args.date)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(out)
    log(f"✓ 已生成 {dst}  ({os.path.getsize(dst) / 1024:.1f} KB · {data['total']} 条)", C_GRN)
    return 0


# ---------------------------------------------------------------- assemble
# 把 data/raw/ 下分头检索的产出拼装成当日 data/<date>.json
RAW_FILES = [
    ("ai_coding.json", "AI Coding", True, "list"),
    ("embodied.json", "具身智能", True, "list"),
    ("weekly_tibo.json", "一周大事", False, "weekly"),
    ("companies.json", "Anthropic（A社）", False, "anthropic"),
    ("companies.json", "OpenAI", False, "openai"),
    ("weekly_tibo.json", "Tibo · Codex 额度重置信号", False, "tibo"),
]


def _load_raw(raw_dir: str, fname: str):
    p = os.path.join(raw_dir, fname)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def cmd_assemble(args) -> int:
    raw_dir = args.raw_dir or os.path.join(DATA_DIR, "raw")
    if not os.path.isdir(raw_dir):
        die(f"原始目录不存在：{raw_dir}")

    skeleton = {lbl: (focus, hint) for lbl, focus, hint in DEFAULT_SKELETON}
    cache: dict[str, object] = {}
    sections, missing = [], []

    for fname, label, focus, key in RAW_FILES:
        if fname not in cache:
            cache[fname] = _load_raw(raw_dir, fname)
        blob = cache[fname]
        if blob is None:
            missing.append(f"{label}（缺 {fname}）")
            continue
        items = blob if key == "list" else (blob or {}).get(key)
        if not isinstance(items, list):
            missing.append(f"{label}（{fname} 里没有 {key}）")
            continue
        # 去掉空条目
        items = [i for i in items if isinstance(i, dict) and (i.get("title") or "").strip()]
        if not items:
            missing.append(f"{label}（条目为空）")
            continue
        for it in items:
            it.pop("n", None)                      # 序号交给渲染层统一分配
            it.setdefault("summaryFull", it.get("summary", ""))
        hint = skeleton.get(label, (focus, ""))[1]
        sections.append({"label": label, "focus": focus, "hint": hint, "items": items})

    if missing:
        msg = "以下板块缺少检索产出：\n    - " + "\n    - ".join(missing)
        if not args.allow_missing:
            die(msg + "\n  （加 --allow-missing 可先生成残缺版本）")
        log("⚠ " + msg.replace("\n    ", "\n    "), C_YEL)

    if not sections:
        die("没有任何可用板块")

    # highlights：优先 data/raw/highlights.json，其次 --highlights "a|b|c"
    highlights = []
    hl_file = os.path.join(raw_dir, "highlights.json")
    if os.path.exists(hl_file):
        with open(hl_file, encoding="utf-8") as fh:
            hl = json.load(fh)
        highlights = hl if isinstance(hl, list) else hl.get("highlights", [])
    elif args.highlights:
        highlights = [h.strip() for h in args.highlights.split("|") if h.strip()]
    highlights = [h for h in highlights if h and h.strip()]

    day = dt.date.fromisoformat(args.date)
    ws = day - dt.timedelta(days=int(args.window_days) - 1)
    obj = {
        "date": args.date,
        "reportName": "AI 晨报",
        "source": "AI HOT",
        "canonical": f"https://aihot.virxact.com/daily/{args.date}",
        "eyebrow": "AI HOT · 每日晨报",
        "focusNote": "侧重方向：AI Coding 与 具身智能",
        "windowStart": ws.isoformat() + "T00:00:00+08:00",
        "windowEnd": args.date + "T23:59:00+08:00",
        "focusWindowDays": FOCUS_MAX_DAYS,
        "generalWindowDays": int(args.window_days),
        "highlights": highlights,
        "sections": sections,
    }
    obj = normalize(obj, args.date)

    if os.path.exists(data_path(args.date)) and not args.force:
        die(f"{data_path(args.date)} 已存在（加 --force 覆盖）")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(data_path(args.date), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    log(f"✓ 已拼装 {data_path(args.date)}：{obj['total']} 条 / {len(sections)} 板块", C_GRN)
    for s in sections:
        log(f"    {s['label']:<26} {len(s['items'])} 条")
    if not highlights:
        log("  ⚠ 没有 highlights（本期主线卡片会隐藏）", C_YEL)
    return 0


# ---------------------------------------------------------------- index
def scan_reports() -> list[dict]:
    """扫描根目录所有报告，从文件名取日期、从内嵌 DATA 取元信息。"""
    out = []
    for fn in os.listdir(ROOT):
        m = REPORT_GLOB.match(fn)
        if not m:
            continue
        p = os.path.join(ROOT, fn)
        date = m.group(1)
        meta = {"date": date, "file": fn, "total": None, "highlights": [], "sections": []}
        try:
            with open(p, encoding="utf-8") as fh:
                head = fh.read(400_000)
            dm = DATA_RE.search(head)
            if dm:
                d = json.loads(dm.group(1))
                meta["total"] = d.get("total") or sum(len(s.get("items", [])) for s in d.get("sections", []))
                meta["highlights"] = d.get("highlights", [])[:1]
                meta["sections"] = [s.get("label") for s in d.get("sections", [])]
                meta["reportName"] = d.get("reportName", "AI 晨报")
        except Exception:
            pass
        out.append(meta)
    # 按文件名日期降序（不按 mtime——重跑旧报告不应打乱顺序）
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


def cmd_index(args) -> int:
    reports = scan_reports()
    if not reports:
        log("没有找到任何 AI新闻速览_*.html", C_YEL)

    groups: dict[str, list] = {}
    for r in reports:
        groups.setdefault(r["date"][:7], []).append(r)

    blocks = []
    for ym in sorted(groups, reverse=True):
        items = groups[ym]
        lis = []
        for r in items:
            total = f"{r['total']} 条" if r["total"] else ""
            hl = html.escape(r["highlights"][0]) if r["highlights"] else ""
            hl_html = f'<p class="hl">{hl}</p>' if hl else ""
            lis.append(
                f'<li><a href="{html.escape(r["file"])}">'
                f'<span class="d">{r["date"]}</span>'
                f'<span class="n">{total}</span>{hl_html}</a></li>'
            )
        blocks.append(f'<h2>{ym.replace("-", " 年 ")} 月</h2>\n<ul class="list">\n' + "\n".join(lis) + "\n</ul>")

    latest = reports[0] if reports else None
    latest_btn = (f'<a class="btn" href="{html.escape(latest["file"])}">查看最新一期 · {latest["date"]} →</a>'
                  if latest else "")
    cnt = len(reports)
    body = "\n".join(blocks) or '<p style="color:#8a7fb0">暂无报告</p>'

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 新闻速览 · 归档</title>
<meta name="description" content="每日 AI Coding / 具身智能晨报归档，共 {cnt} 期">
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",sans-serif;background:#f5f3fb;color:#1c1430}}
  .hero{{background:linear-gradient(135deg,#3b1d6e 0%,#5b2a9e 45%,#7b3fd1 100%);color:#fff;padding:48px 24px 40px;text-align:center}}
  .hero h1{{font-size:30px;font-weight:800}}
  .hero .sub{{margin-top:10px;opacity:.85;font-size:15px}}
  .hero .badge{{display:inline-block;margin-top:18px;background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.35);padding:6px 16px;border-radius:999px;font-size:14px;font-weight:600}}
  .hero .btn{{display:inline-block;margin-top:22px;background:#fff;color:#4a1d8a;text-decoration:none;padding:11px 26px;border-radius:10px;font-weight:700;font-size:15px;box-shadow:0 6px 20px rgba(0,0,0,.2)}}
  .wrap{{max-width:860px;margin:0 auto;padding:32px 20px 60px}}
  h2{{font-size:15px;color:#7b6ba8;margin:26px 0 12px;letter-spacing:.06em;text-transform:uppercase}}
  ul{{list-style:none}}
  .list li{{margin-bottom:12px}}
  .list a{{display:block;background:#fff;border:1px solid #e7e0f5;border-radius:12px;padding:14px 18px;text-decoration:none;color:#3b1d6e;transition:.15s}}
  .list a:hover{{transform:translateY(-2px);box-shadow:0 8px 22px rgba(91,42,158,.12)}}
  .list .d{{font-weight:800;font-size:16px}}
  .list .n{{margin-left:10px;font-size:12.5px;color:#8a7fb0;background:#f2eefb;border-radius:999px;padding:2px 10px;font-weight:700}}
  .list .hl{{margin-top:8px;font-size:13.5px;color:#6b5f8f;font-weight:400;line-height:1.5}}
  footer{{text-align:center;color:#9a8fc0;font-size:13px;padding:24px}}
</style>
</head>
<body>
<div class="hero">
  <h1>🤖 AI 新闻速览 · 归档</h1>
  <div class="sub">每日 AI Coding / 具身智能晨报</div>
  <div class="badge">共 {cnt} 期</div>
  <br>{latest_btn}
</div>
<div class="wrap">
{body}
</div>
<footer>由 newspipe 生成 · {dt.datetime.now(BJ).strftime("%Y-%m-%d %H:%M")}</footer>
</body>
</html>"""

    dst = os.path.join(ROOT, "index.html")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(doc)
    log(f"✓ 已生成 {dst}（{cnt} 期）", C_GRN)

    # RSS
    rss_items = []
    for r in reports[:30]:
        link = html.escape(r["file"])
        desc = html.escape(r["highlights"][0] if r["highlights"] else f"{r['total']} 条 AI 动态")
        pub = dt.datetime.strptime(r["date"], "%Y-%m-%d").replace(tzinfo=BJ).strftime("%a, %d %b %Y 00:00:00 +0800")
        rss_items.append(f"<item><title>{html.escape(r['date'])} AI 晨报（{r['total']} 条）</title>"
                         f"<link>{link}</link><guid>{link}</guid><pubDate>{pub}</pubDate>"
                         f"<description>{desc}</description></item>")
    rss = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           '<title>AI 新闻速览</title><description>每日 AI Coding / 具身智能晨报</description>'
           f'<language>zh-CN</language>\n' + "\n".join(rss_items) + "\n</channel></rss>")
    with open(os.path.join(ROOT, "feed.xml"), "w", encoding="utf-8") as fh:
        fh.write(rss)
    log(f"✓ 已生成 {os.path.join(ROOT, 'feed.xml')}", C_GRN)
    return 0


# ---------------------------------------------------------------- links
def cmd_check_links(args) -> int:
    data = load_data(args.date)
    urls = []
    for sec in data.get("sections", []):
        for it in sec.get("items", []):
            u = it.get("sourceUrl")
            if u and u not in urls:
                urls.append(u)
    log(f"检查 {len(urls)} 个链接…\n")
    bad = 0
    for u in urls:
        code, note = "—", ""
        try:
            req = urllib.request.Request(u, method="HEAD", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/125 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=15) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
            if e.code in (403, 405, 503):  # 反爬常拦 HEAD，再试 GET
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": req.get_header("User-agent")})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        code, note = r.status, "(GET)"
                except Exception as e2:
                    code, note = getattr(e2, "code", "ERR"), f"({type(e2).__name__})"
        except Exception as e:
            code, note = "ERR", f"({type(e).__name__})"
        ok = isinstance(code, int) and code < 400
        if not ok:
            bad += 1
        log(f"  {'✓' if ok else '✗'} {code} {note} {u}", C_GRN if ok else C_RED)
    log("")
    if bad:
        log(f"{bad}/{len(urls)} 个链接不可达（403/404 可能只是反爬，需人工确认）", C_YEL)
        return 0
    log(f"全部 {len(urls)} 个链接可达", C_GRN)
    return 0


# ---------------------------------------------------------------- new
BLANK = {
    "date": "",
    "reportName": "AI 晨报",
    "source": "AI HOT",
    "canonical": "",
    "eyebrow": "AI HOT · 每日晨报",
    "focusNote": "侧重方向：AI Coding 与 具身智能",
    "windowStart": "",
    "windowEnd": "",
    "focusWindowDays": FOCUS_MAX_DAYS,
    "generalWindowDays": GENERAL_MAX_DAYS,
    "highlights": ["", "", ""],
    "sections": [],
}


def cmd_new(args) -> int:
    d = args.date
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        die("--date 需要 YYYY-MM-DD")
    if os.path.exists(data_path(d)):
        die(f"已存在：{data_path(d)}")
    day = dt.date.fromisoformat(d)
    ws = day - dt.timedelta(days=GENERAL_MAX_DAYS - 1)
    obj = json.loads(json.dumps(BLANK))
    obj["date"] = d
    obj["windowStart"] = ws.isoformat() + "T00:00:00+08:00"
    obj["windowEnd"] = d + "T23:59:00+08:00"
    obj["canonical"] = f"https://aihot.virxact.com/daily/{d}"
    obj["sections"] = [
        {"label": lbl, "focus": focus, "hint": hint,
         "items": [{"title": "", "summary": "", "summaryFull": "", "sourceName": "",
                    "sourceUrl": "", "publishedAt": ""} for _ in range(4)]}
        for lbl, focus, hint in DEFAULT_SKELETON
    ]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(data_path(d), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log(f"✓ 已创建 {data_path(d)}（骨架 {len(obj['sections'])} 板块）", C_GRN)
    return 0


# ---------------------------------------------------------------- all
def cmd_all(args) -> int:
    rc = cmd_validate(args)
    if rc != 0 and not args.force:
        die("校验未通过，已中止（加 --force 可强行继续）")
    cmd_render(args)
    return cmd_index(args)


def main() -> int:
    ap = argparse.ArgumentParser(description="每日 AI 新闻速览 工具链", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, needs_date in (("all", cmd_all, True), ("validate", cmd_validate, True),
                                 ("render", cmd_render, True), ("check-links", cmd_check_links, True),
                                 ("new", cmd_new, True), ("assemble", cmd_assemble, True),
                                 ("index", cmd_index, False)):
        p = sub.add_parser(name, help=fn.__doc__ or "")
        if needs_date:
            p.add_argument("--date", required=True, help="报告日期 YYYY-MM-DD")
        if name in ("render", "all"):
            p.add_argument("--force", action="store_true", help="校验失败也继续")
            p.add_argument("--out", help="输出 HTML 路径（默认根目录）")
        if name == "assemble":
            p.add_argument("--raw-dir", help="原始检索产出目录（默认 data/raw）")
            p.add_argument("--highlights", help="本期主线，用 | 分隔")
            p.add_argument("--window-days", default=GENERAL_MAX_DAYS, help="收录窗口天数（默认 7）")
            p.add_argument("--allow-missing", action="store_true", help="缺板块也生成")
            p.add_argument("--force", action="store_true", help="覆盖已存在的日期文件")
        p.set_defaults(func=fn)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
