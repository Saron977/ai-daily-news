# 每日 AI 新闻速览 · newspipe

把「每日 AI 晨报」从**每天手写一个 HTML**，改成**内容（JSON）+ 渲染（模板）+ 校验（审计）**的工具链。

## 为什么改

原工作流（`~/Desktop/丶/documentation/workBuddy/尝鲜/`）里没有真正的生成脚本——每天由 LLM 直接手写
一个内嵌 `DATA` 的 HTML，只有 `gen_publish.py` 负责复制文件做部署。这带来几类反复踩到的坑：

| 老问题 | 表现 | 现在怎么解 |
|---|---|---|
| 无内容/渲染分离 | 每天重写 30KB HTML，样式与逻辑容易漂移 | 内容进 `data/*.json`，HTML 由 `tools/template.html` 渲染 |
| 新鲜度失控 | 焦点板块混入 5–10 天前旧闻，用户多次复检打回 | `validate` 强制审计：**焦点板块 ≤3 天 / 其余 ≤7 天**，越界即报错并**阻断渲染** |
| 序号 / 总数对不上 | 手工挪条目后断号、`total` 与实际条数不一致 | 渲染时全局重排序号、总数自动统计 |
| 相对日期渲染错 | `fmtTime` 把所有过去日期都标成「昨天」 | 重写为 `今天/昨天/前天/N天前`，并有 `selftest.js` 断言 |
| 索引按 mtime 排序 | 重跑一份旧报告，它跳到列表最前 | 按**文件名日期**降序；`index.html` + `feed.xml` 自动生成 |
| 路径写死 | `gen_publish.py` 里硬编码绝对路径 | 全部相对仓库根目录，零依赖（仅标准库） |

## 目录结构

```
news/
├── tools/
│   ├── newspipe.py     # 主工具：new / assemble / validate / render / index / check-links / all
│   ├── template.html   # 单文件报告模板（占位符 __DATA__ / __TITLE__ / __DESC__ / __BUILD__）
│   ├── selftest.js     # 渲染产物自检：桩 DOM 执行 JS + 结构 + 新鲜度断言
│   ├── readurl.py      # curl 抓网页抽正文（核实来源；沙箱里 web_fetch 常被 DNS 拦）
│   └── daily.sh        # 一条命令跑完当日流程
├── data/
│   ├── YYYY-MM-DD.json # 每日内容（唯一需要人/LLM 写的文件）
│   └── raw/            # 检索原始产出（assemble 的输入，可留档）
├── _scratch/           # 回归用样例，不参与渲染
├── AI新闻速览_YYYY-MM-DD.html   # 产物，单文件、可离线、可直接发
├── index.html          # 归档索引（按月份分组）
└── feed.xml            # RSS 2.0
```

## 用法

完整流程（推荐）：

```bash
tools/daily.sh 2026-09-14     # 拼装 -> 校验+渲染+索引 -> 产物自检
```

分步：

```bash
# 1) 检索产出放进 data/raw/（见下方「raw 约定」），然后拼装成当日数据文件
python3 tools/newspipe.py assemble --date 2026-09-14 --highlights "主线一|主线二|主线三"

# 2) 只校验，不写文件（查看新鲜度审计表）
python3 tools/newspipe.py validate --date 2026-09-14

# 3) 校验 + 渲染 + 重建索引
python3 tools/newspipe.py all --date 2026-09-14

# 从空白骨架开始（不用 raw，手写 data/*.json 时用）
python3 tools/newspipe.py new --date 2026-09-15

# 其它
python3 tools/newspipe.py render --date 2026-09-14 --force   # 校验失败也渲染
python3 tools/newspipe.py check-links --date 2026-09-14      # 探测来源链接可达性
node tools/selftest.js AI新闻速览_2026-09-14.html            # 产物自检
python3 tools/readurl.py "<url>" --grep "关键词"             # 核实来源
```

退出码：`0` 通过、`1` 校验有错误、`2` 数据缺陷或渲染被拒——方便挂到 CI / 定时任务上。

### raw 约定（`data/raw/`）

`assemble` 按固定文件名拼装，缺失的板块会报错（`--allow-missing` 可放行）：

| 文件 | 键 | 去向板块 |
|---|---|---|
| `ai_coding.json` | 数组（4 条） | AI Coding（焦点） |
| `embodied.json` | 数组（4 条） | 具身智能（焦点） |
| `weekly_tibo.json` | `{"weekly": […5], "tibo": […5]}` | 一周大事 / Tibo |
| `companies.json` | `{"anthropic": […4], "openai": […4]}` | Anthropic（A社）/ OpenAI |
| `highlights.json` | 数组（可选） | 「📌 本期主线」卡片 |


## 数据格式

```jsonc
{
  "date": "2026-09-14",
  "reportName": "AI 晨报",
  "source": "AI HOT",
  "canonical": "https://aihot.virxact.com/daily/2026-09-14",
  "eyebrow": "AI HOT · 每日晨报",
  "focusNote": "侧重方向：AI Coding 与 具身智能",
  "windowStart": "2026-09-08T00:00:00+08:00",
  "windowEnd":   "2026-09-14T23:59:00+08:00",
  "focusWindowDays": 3,      // focus 板块的新鲜度上限
  "generalWindowDays": 7,    // 其余板块的上限
  "highlights": ["本期主线一", "本期主线二"],   // 渲染成「📌 本期主线」卡片
  "sections": [
    {
      "label": "AI Coding",
      "focus": true,          // true → 走 3 天窗口，并在 validate 里单独标注
      "hint": "每日焦点 · 近 3 天",
      "items": [
        {
          "n": 1,             // 可省略：渲染时会按顺序全局重排
          "title": "…",
          "summary": "…【值得关注】…",     // 校验会要求这句解读
          "summaryFull": "…",             // 省略则回退为 summary
          "sourceName": "Reuters",
          "sourceUrl": "https://具体文章链接",   // 首页会被警告，重复会被判错
          "publishedAt": "2026-09-13T08:00:00.000Z"   // ISO8601 UTC
        }
      ]
    }
  ]
}
```

`total` 不需要手填（会被重算）。板块与顺序默认对齐 6 板块骨架：
AI Coding / 具身智能 / 一周大事 / Anthropic（A社）/ OpenAI / Tibo · Codex 额度重置信号。

## validate 会检查什么

**错误（阻断交付）**：`date` 不匹配、板块为空、缺必填字段、`sourceUrl` 非 http(s)、URL 重复、
`publishedAt` 不可解析、**超出新鲜度窗口**、`total` 与实际条数不一致。

**警告（提示但放行）**：标题过长、标题重复、来源是首页而非具体文章、摘要缺少【值得关注】、
摘要长度异常、`publishedAt` 晚于报告日、板块骨架与默认不一致。

## 报告页特性

单文件 HTML、零外部请求、可离线打开。在原设计基础上加了：

- **📌 本期主线**卡片（来自 `highlights`）
- **搜索过滤**：多关键词、空格分隔、`/` 聚焦、`Esc` 清空；板块计数随过滤变成 `命中/总数`
- **深色模式**：跟随系统 + `◐` 手动切换（`localStorage` 记忆）
- **新鲜度徽标**：每条显示 `窗口内` / `N 天前`
- **条目永久链接**：序号即锚点（`#n12`），`target` 高亮
- **打印样式**：隐藏导航与按钮，双栏排版，可直接存 PDF
- **回到顶部**、Hero 统计可点击跳转、`meta description` / Open Graph

## 日常节奏

1. 检索：聚焦板块只取报告日前 1–3 天，其余板块取 7 天窗口
2. 写 `data/YYYY-MM-DD.json`
3. `python3 tools/newspipe.py all --date YYYY-MM-DD`
4. `node tools/selftest.js AI新闻速览_YYYY-MM-DD.html`
5. 交付 HTML 路径（`index.html` 会自动把新一期排在最前）
