#!/usr/bin/env node
/**
 * 模板自检：从已渲染的报告 HTML 中抽出脚本，在桩 DOM 下执行，
 * 校验 (1) DATA 可解析 (2) JS 无语法/运行错误 (3) 序号连续 (4) 相对日期文案正确。
 *
 *   node tools/selftest.js AI新闻速览_2026-09-14.html
 */
const fs = require("fs");
const path = require("path");

const file = process.argv[2];
if (!file) { console.error("用法: node tools/selftest.js <报告.html>"); process.exit(2); }
const html = fs.readFileSync(path.resolve(file), "utf8");

const script = html.match(/<script>([\s\S]*?)<\/script>/);
if (!script) { console.error("✗ 找不到 <script> 块"); process.exit(1); }
const js = script[1];

const dm = js.match(/^const DATA = (\{.*\});$/m);
if (!dm) { console.error("✗ 找不到内嵌 DATA（索引页依赖单行 DATA，勿手工折行）"); process.exit(1); }
const DATA = JSON.parse(dm[1]);

let fail = 0;
const ok = (c, m) => { console.log(`  ${c ? "✓" : "✗"} ${m}`); if (!c) fail++; };

// ---- 结构 ----
const serials = DATA.sections.flatMap(s => s.items.map(i => i.n));
const total = DATA.sections.reduce((a, s) => a + s.items.length, 0);
ok(total === DATA.total, `total 一致：${DATA.total} == ${total}`);
ok(serials.every((n, i) => n === i + 1), `序号连续 1..${total}`);
ok(DATA.sections.every(s => s.items.every(i => i.title && i.sourceUrl && i.publishedAt)),
   "必要字段齐备");
const urls = DATA.sections.flatMap(s => s.items.map(i => i.sourceUrl));
ok(new Set(urls).size === urls.length, `来源链接无重复（${urls.length} 个）`);
ok(urls.every(u => /^https?:\/\//.test(u)), "全部为 http(s) 链接");

// ---- 新鲜度 ----
const BJ = 8 * 3600 * 1000;
const ref = Date.UTC(...DATA.date.split("-").map((x, i) => i === 1 ? +x - 1 : +x));
for (const s of DATA.sections) {
  const limit = s.focus ? (DATA.focusWindowDays || 3) : (DATA.generalWindowDays || 7);
  const ages = s.items.map(i => Math.round((ref - Math.floor((new Date(i.publishedAt).getTime() + BJ) / 86400000) * 86400000) / 86400000));
  const mx = Math.max(...ages);
  ok(mx <= limit, `${s.label}：最旧 ${mx} 天 ≤ ${limit} 天`);
}

// ---- 在桩 DOM 下执行 ----
const els = {};
const mk = (id) => ({
  id, textContent: "", innerHTML: "", style: {}, dataset: {}, children: [], kids: [],
  classList: { toggle() {}, add() {}, remove() {} },
  appendChild(c) { this.kids.push(c); return c; },
  addEventListener() {}, querySelectorAll() { return []; }, querySelector() { return null; },
  insertBefore() {}, focus() {}, blur() {}, dispatchEvent() {}, href: "",
});
global.document = {
  getElementById: (id) => els[id] || (els[id] = mk(id)),
  createElement: mk, querySelector: () => mk("main"), querySelectorAll: () => [],
  addEventListener() {}, documentElement: { dataset: {} },
};
global.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, scrollTo() {}, scrollY: 0 };
global.localStorage = { getItem: () => null, setItem() {} };
global.IntersectionObserver = class { observe() {} };
global.Event = class { constructor(t) { this.type = t; } };

let runtimeErr = null;
const sandbox = {};
try {
  // 暴露 DATA 供函数使用，并导出 fmtTime 以便断言
  new Function("sandbox", js + "\n;sandbox.fmtTime=fmtTime;sandbox.fmtWindow=fmtWindow;")(sandbox);
} catch (e) { runtimeErr = e; }
ok(!runtimeErr, `脚本执行无错误${runtimeErr ? "：" + runtimeErr.message : ""}`);
if (runtimeErr) { console.log(`\n✗ 自检失败（${fail} 项）`); process.exit(1); }

const fmtTime = sandbox.fmtTime;
ok(typeof fmtTime === "function", "fmtTime 可用");

// ---- 相对日期文案（旧模板会把所有过去日期误标为「昨天」） ----
// 约定：相对标签 + 绝对日期同时给出（报告多日后回看仍可定位）；>2 天前只给绝对日期。
// 断言按报告日动态生成，换日期也会执行（早期写死 9/14，换一天就整段跳过，等于没测）。
const [RY, RM, RD] = DATA.date.split("-").map(Number);
const dayMs = 86400000;
const shift = (n) => new Date(Date.UTC(RY, RM - 1, RD) - n * dayMs);
const isoAt = (d, utcHour) => new Date(d.getTime() + (utcHour - 8) * 3600000).toISOString()
  .replace(/\.\d{3}Z$/, ".000Z");                    // 反推 UTC 时刻，使北京时间正好落在整点
const label = (d, period, hh) =>
  `${d.getUTCMonth() + 1}月${d.getUTCDate()}日 ${period}${hh}:00`;

const d0 = shift(0), d1 = shift(1), d2 = shift(2), d4 = shift(4);
const cases = [
  [isoAt(d0, 10), `今天 ${label(d0, "上午", 10)}`],
  [isoAt(d1, 10), `昨天 ${label(d1, "上午", 10)}`],
  [isoAt(d2, 10), `前天 ${label(d2, "上午", 10)}`],
  [isoAt(d4, 10), label(d4, "上午", 10)],            // >2 天只给绝对日期，不带相对标签
  [isoAt(d0, 22), `今天 ${label(d0, "晚上", 22)}`],   // 时段词：晚上
  [isoAt(d1, 4),  `昨天 ${label(d1, "凌晨", 4)}`],    // 时段词：凌晨
];
for (const [iso, want] of cases) {
  const got = fmtTime(iso, DATA.date).text;
  ok(got === want, `fmtTime(${iso}) → ${got}${got === want ? "" : `（期望 ${want}）`}`);
}
// 跨零点：北京日期必须按 UTC+8 判定，而不是直接用 UTC 日
const crossIso = isoAt(d0, 4);                       // 北京 d0 04:00 == UTC 前一日 20:00
ok(crossIso.slice(0, 10) !== DATA.date,
   `跨零点用例确实跨了 UTC 日（${crossIso}）`);
ok(fmtTime(crossIso, DATA.date).text.startsWith("今天"),
   `跨零点判为今天：${fmtTime(crossIso, DATA.date).text}`);

// ---- 渲染产物 ----
ok(String(els["hero-total"] && els["hero-total"].textContent) === String(total), `Hero 总数 = ${total}`);
ok((els["main"].kids || []).length >= DATA.sections.length + 1, "正文板块已挂载");

console.log(fail ? `\n✗ 自检失败（${fail} 项）` : `\n✓ 自检通过（${total} 条 / ${DATA.sections.length} 板块）`);
process.exit(fail ? 1 : 0);
