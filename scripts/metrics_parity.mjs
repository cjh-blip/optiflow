/**
 * 跨语言一致性断言（数据对数据）：同一份（结果包, 判据表）→
 *   JS  `metricsBoard`（ui/assets/metrics.js）
 *   Py  `report.metrics`（optiflow 动作注册表 → report_assembler.metrics_rows）
 * 必须算出**逐格相同**的六项指标；另外再查一遍「报告里那张表」与数据是否一致（渲染不能漂）。
 *
 * 归属：本脚本属平台侧（口径要统一的是这里），但 JS 实现住在 Hana App 仓
 *   （`optiflow-workbench/ui/assets/metrics.js`），所以它是**跨仓脚本** —— 用
 *   `GUANGSHU_METRICS_JS` 指过去（默认本机路径）。将来若两仓进 CI，两边都要有这份脚本的检出。
 *
 * 跑法：
 *   node scripts/metrics_parity.mjs            # 带判据表 + 不带判据表，各验一遍
 *   node scripts/metrics_parity.mjs --no-criteria-only   # 只验不带判据表那组
 * 依赖：Node ≥ 18、workshop Python（或 GUANGSHU_PY 指定）。
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { delimiter } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// 路径一律由环境变量给，脚本里不写任何本机路径（这是会公开的仓）。
//   GUANGSHU_METRICS_JS  JS 侧实现（Hana App 仓的 ui/assets/metrics.js）—— 必填
//   GUANGSHU_PKG        结果包 JSON —— 必填（自备一份即可，格式见 optiflow/spec/）
//   GUANGSHU_CRIT       判据表 JSON —— 可选；不给就只跑「无判据表」那组
//   GUANGSHU_PY         Python 解释器 —— 默认 python3
//   GUANGSHU_REPO       optiflow 仓库根 —— 默认取本脚本的上一级目录
const PY = process.env.GUANGSHU_PY || "python3";
const OPTIFLOW_REPO = process.env.GUANGSHU_REPO
  || fileURLToPath(new URL("..", import.meta.url)).replace(/[/\\]$/, "");

function need(name, hint) {
  const v = process.env[name];
  if (!v) {
    console.error(`缺少环境变量 ${name}：${hint}`);
    process.exit(2);
  }
  return v;
}

const METRICS_JS = need("GUANGSHU_METRICS_JS", "JS 侧 metrics.js 的路径（在 Hana App 仓里）");
const PKG = need("GUANGSHU_PKG", "结果包 JSON 的路径");
const CRIT = process.env.GUANGSHU_CRIT || "";

const { metricsBoard } = await import(pathToFileURL(METRICS_JS).href);

let failures = 0;
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "✓" : "✗"} ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failures++;
};

/** 调动作注册表（顺便验证表能派发、以及 Py 侧数据面真的存在） */
function pyCall(commandId, params) {
  const argv = ["-m", "optiflow.commands", "call", commandId, "--params", JSON.stringify(params)];
  const out = execFileSync(PY, argv, {
    cwd: OPTIFLOW_REPO, encoding: "utf-8",
    env: { ...process.env, PYTHONPATH: [`${OPTIFLOW_REPO}/src`, OPTIFLOW_REPO].join(delimiter) },
  });
  const payload = JSON.parse(out.trim().split("\n").pop());
  if (!payload.ok) throw new Error(payload.error);
  return payload.result;
}

/** 报告 Markdown 里「2. 照明效果」那张表 → { 指标: {实测值, 判据阈值, 判定} } */
function parseEffectTable(markdown) {
  const rows = {};
  let inBlock = false;
  for (const line of markdown.split("\n")) {
    if (line.startsWith("## 2.")) { inBlock = true; continue; }
    if (inBlock && line.startsWith("## ")) break;
    if (!inBlock || !line.trim().startsWith("|")) continue;
    const cells = line.split("|").map((c) => c.trim()).filter((c) => c !== "");
    if (cells.length < 4 || cells[0] === "指标" || cells[0].startsWith("---")) continue;
    rows[cells[0]] = { 实测值: cells[1], 判据阈值: cells[2], 判定: cells[3] };
  }
  return rows;
}

const COLS = ["valueText", "threshold", "verdict", "source"];
const CN = { valueText: "实测值", threshold: "判据阈值", verdict: "判定", source: "出处" };

// 2026-10-28 增：判据溯源三格（条款号 / 印刷页 / 条文原文）。
// 嵌套取值用路径数组，与顶层列同一套比对逻辑。
const EV_COLS = [
  ["evidence", "clause"],
  ["evidence", "page_print"],
  ["evidence", "quote"],
];
for (const path of EV_COLS) CN[path.join(".")] = path.join(".");

const pick = (row, col) => (Array.isArray(col)
  ? col.reduce((o, k) => (o === null || o === undefined ? undefined : o[k]), row)
  : row[col]);

function compareJsVsPy(tag, jsRows, pyRows) {
  const byLabel = Object.fromEntries(jsRows.map((r) => [r.label, r]));
  const labels = Object.keys(byLabel);
  check(`${tag} 两侧行数一致`, labels.length === pyRows.length && labels.every((l) => pyRows.some((p) => p.label === l)),
        `JS ${labels.length} 行 / Py ${pyRows.length} 行`);

  const cols = [...COLS, ...EV_COLS];
  let diffs = 0;
  for (const label of labels) {
    const py = pyRows.find((p) => p.label === label);
    if (!py) { console.log(`  ✗ Py 侧缺「${label}」`); diffs++; continue; }
    for (const col of cols) {
      const jsv = pick(byLabel[label], col);
      const pyv = pick(py, col);
      if (jsv !== pyv) {
        console.log(`  ✗ ${label} · ${CN[Array.isArray(col) ? col.join(".") : col]}：JS「${jsv}」 vs Py「${pyv}」`);
        diffs++;
      }
    }
  }
  check(`${tag} 逐格相同`, diffs === 0, diffs ? `${diffs} 处不同` : `${labels.length * cols.length} 格全同（${labels.length} 行 × ${cols.length} 列）`);
  return diffs;
}

function compareReportVsData(tag, markdown, pyRows) {
  const table = parseEffectTable(markdown);
  let diffs = 0;
  for (const row of pyRows) {
    const t = table[row.label];
    if (!t) { console.log(`  ✗ 报告里缺「${row.label}」`); diffs++; continue; }
    if (t.实测值 !== row.valueText || t.判据阈值 !== row.threshold || t.判定 !== row.verdict) {
      console.log(`  ✗ 报告「${row.label}」与数据不符：报告「${t.实测值}/${t.判据阈值}/${t.判定}」 vs 数据「${row.valueText}/${row.threshold}/${row.verdict}」`);
      diffs++;
    }
  }
  check(`${tag} 报告渲染与数据一致`, diffs === 0, diffs ? `${diffs} 处不同` : "6 行全同");
}

const pkg = JSON.parse(fs.readFileSync(PKG, "utf-8"));
const crit = CRIT ? JSON.parse(fs.readFileSync(CRIT, "utf-8")) : null;
const onlyNoCriteria = process.argv.includes("--no-criteria-only");

console.log(`JS：${METRICS_JS.split("/").slice(-3).join("/")}`);
console.log(`Py：optiflow.commands call report.metrics（cwd=${OPTIFLOW_REPO}）\n`);

if (!onlyNoCriteria && crit) {
  const jsRows = metricsBoard(pkg, crit).rows;
  const py = pyCall("report.metrics", { package: PKG, criteria: CRIT });
  compareJsVsPy("【带判据表】", jsRows, py.rows);
  compareReportVsData("【带判据表】", pyCall("report.assemble", { package: PKG, criteria: CRIT }).markdown, py.rows);
  console.log();
}

const jsNoCrit = metricsBoard(pkg, null).rows;
const pyNoCrit = pyCall("report.metrics", { package: PKG }).rows;
compareJsVsPy("【无判据表】", jsNoCrit, pyNoCrit);

console.log(failures === 0 ? "\n一致性断言通过" : `\n一致性断言未过：${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
