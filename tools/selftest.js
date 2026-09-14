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
// 最后一条覆盖跨零点：UTC 9/13 20:00 = 北京 9/14 04:00，必须判为「今天」。
const cases = [
  ["2026-09-14T02:00:00.000Z", "今天 9月14日 上午10:00"],
  ["2026-09-13T02:00:00.000Z", "昨天 9月13日 上午10:00"],
  ["2026-09-12T02:00:00.000Z", "前天 9月12日 上午10:00"],
  ["2026-09-11T02:00:00.000Z", "9月11日 上午10:00"],
  ["2026-09-03T14:00:00.000Z", "9月3日 晚上22:00"],
  ["2026-09-13T20:00:00.000Z", "今天 9月14日 凌晨4:00"],
];
if (DATA.date === "2026-09-14") {
  for (const [iso, want] of cases) {
    const got = fmtTime(iso, DATA.date).text;
    ok(got === want, `fmtTime(${iso}) → ${got}${got === want ? "" : `（期望 ${want}）`}`);
  }
} else {
  console.log(`  · 跳过日期文案断言（报告日为 ${DATA.date}，断言集针对 2026-09-14）`);
}

// ---- 渲染产物 ----
ok(String(els["hero-total"] && els["hero-total"].textContent) === String(total), `Hero 总数 = ${total}`);
ok((els["main"].kids || []).length >= DATA.sections.length + 1, "正文板块已挂载");

console.log(fail ? `\n✗ 自检失败（${fail} 项）` : `\n✓ 自检通过（${total} 条 / ${DATA.sections.length} 板块）`);
process.exit(fail ? 1 : 0);
