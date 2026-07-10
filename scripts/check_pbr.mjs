#!/usr/bin/env node
/**
 * PBR (株価純資産倍率) 算出のユニットテスト。
 * frontend/app.js は内部関数を外部エクスポートしないため、smoke_ui.mjs と同じ
 * 手法 (jsdom 上で実スクリプトを評価し実DOMを検証) で PBR=時価総額÷自己資本の
 * 計算結果を検証する。frontend/data/*.json (実データ) をフィクスチャに使う。
 *   node scripts/check_pbr.mjs
 * 検証: 1) 自己資本が正の銘柄でPBRが期待値どおり表示 2) BSデータ欠損銘柄で
 * "-"表示・例外なし 3) 債務超過銘柄で"-"表示・例外なし 4) スクリーナーのPBR列
 * 5) 比較画面のPBR行
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FE = path.join(ROOT, "frontend");
const errors = [];
const fail = (msg) => errors.push(msg);

function checkNoErrors(label) {
  if (!errors.length) return;
  console.error(`PBR CHECK FAILED (${label} で例外発生)`);
  for (const e of errors) console.error("  - " + e);
  process.exit(1);
}

const html = fs.readFileSync(path.join(FE, "index.html"), "utf8");
const localApiSrc = fs.readFileSync(path.join(FE, "local-api.js"), "utf8");
const appSrc = fs.readFileSync(path.join(FE, "app.js"), "utf8");
const dom = new JSDOM(html, {
  url: "https://kojit1229.github.io/stock_analyze/frontend/index.html",
  runScripts: "outside-only", pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;

window.addEventListener("error", (e) => fail("window error: " + (e.error?.stack || e.message)));
window.addEventListener("unhandledrejection", (e) => fail("unhandledrejection: " + (e.reason?.stack || e.reason)));
Object.assign(window.console, { error: (...a) => fail("console.error: " + a.map(String).join(" ")), warn() {}, log() {} });
Object.assign(window, { alert() {}, confirm: () => false, prompt: () => null, scrollTo() {} });
if (!window.matchMedia) window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
Object.assign(window.URL, { createObjectURL: () => "blob:stub", revokeObjectURL() {} });

window.fetch = async (input) => {
  const m = String(input).match(/data\/([^?]+\.json)/);
  if (!m) return new Response("<html>404</html>", { status: 404, headers: { "Content-Type": "text/html" } });
  const fp = path.join(FE, "data", m[1]);
  if (!fs.existsSync(fp)) return new Response("null", { status: 404, headers: { "Content-Type": "application/json" } });
  return new Response(fs.readFileSync(fp, "utf8"), { status: 200, headers: { "Content-Type": "application/json" } });
};

for (const [name, src] of [["local-api.js", localApiSrc], ["app.js", appSrc]]) {
  try { window.eval(src); } catch (e) { fail(`${name} 評価エラー: ` + e.stack); }
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const appEl = () => document.getElementById("app");

async function goto(hash, timeout = 3000) {
  window.location.hash = hash;
  window.dispatchEvent(new window.Event("hashchange"));
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const app = appEl();
    const txt = app ? app.textContent : "";
    if (app && app.children.length > 0 && !txt.includes("読み込み中")) return;
    await tick(40);
  }
}

// フィクスチャから期待値を算出 (b系列: [期末日,流動資産,総資産,流動負債,負債合計,株主資本,投資有価証券])
const stocksFixture = JSON.parse(fs.readFileSync(path.join(FE, "data", "stocks.json"), "utf8"));
const finFixture = JSON.parse(fs.readFileSync(path.join(FE, "data", "financials.json"), "utf8"));
const equityOf = (code) => {
  const b = (finFixture.stocks[code] || {}).b || [];
  const lb = b[b.length - 1];
  return lb && lb[2] != null && lb[4] != null ? lb[2] - lb[4] : null;
};
function expectedPbr(code) {
  const s = stocksFixture.find((x) => x.code === code);
  const eq = equityOf(code);
  return s && s.market_cap && eq > 0 ? s.market_cap / eq : null;
}

// テスト対象銘柄: POSITIVE=自己資本が正 / NO_BS=BS(b系列)欠損 / NEG_EQ=自己資本が負(債務超過)
const POSITIVE = "1301"; // 極洋
const NO_BS = stocksFixture.find((s) => {
  const f = finFixture.stocks[s.code];
  return f && (!f.b || f.b.length === 0) && s.market_cap;
})?.code;
const NEG_EQ = Object.keys(finFixture.stocks).find((code) => {
  const eq = equityOf(code);
  const s = stocksFixture.find((x) => x.code === code);
  return eq != null && eq <= 0 && s && s.market_cap;
});
assert.ok(expectedPbr(POSITIVE) > 0, `フィクスチャ前提: ${POSITIVE} は自己資本が正であること`);
assert.ok(NO_BS, "フィクスチャ前提: BSデータ欠損銘柄が存在すること");
assert.ok(NEG_EQ, "フィクスチャ前提: 自己資本が負の銘柄が存在すること");

const readCardValue = (label) => {
  const card = [...document.querySelectorAll(".card.stat")]
    .find((c) => (c.querySelector(".label")?.textContent || "").includes(label));
  return card ? (card.querySelector(".value")?.textContent || "").trim() : null;
};

async function main() {
  await tick(80);

  // 1-3) 銘柄分析画面: 正の自己資本 / BSデータ欠損 / 債務超過
  await goto("#/analysis/" + POSITIVE);
  const expected = expectedPbr(POSITIVE).toFixed(2) + "倍";
  assert.equal(readCardValue("PBR"), expected, `[analysis] ${POSITIVE} の PBR が ${expected}`);
  await goto("#/analysis/" + NO_BS);
  assert.equal(readCardValue("PBR"), "-", `[analysis] BS欠損銘柄 ${NO_BS} の PBR は "-"`);
  await goto("#/analysis/" + NEG_EQ);
  assert.equal(readCardValue("PBR"), "-", `[analysis] 債務超過銘柄 ${NEG_EQ} の PBR は "-"`);
  checkNoErrors("analysis 画面");
  console.log(`  [OK] analysis: ${POSITIVE}=${expected} / ${NO_BS}=- (BS欠損) / ${NEG_EQ}=- (債務超過)`);

  // 4) スクリーナー画面: PBR列の追加と算出値
  await goto("#/screener");
  await tick(50);
  const capOku = Math.floor(stocksFixture.find((s) => s.code === POSITIVE).market_cap / 1e8);
  document.getElementById("scr_capMin").value = String(capOku - 1);
  document.getElementById("scr_capMax").value = String(capOku + 1);
  document.getElementById("scr_apply").click();
  await tick(80);
  assert.match(appEl().querySelector("thead")?.innerHTML || "", /PBR/, "[screener] PBR列見出しが存在する");
  const row = [...appEl().querySelectorAll("tbody tr")]
    .find((tr) => tr.querySelector(".code-cell")?.textContent.trim() === POSITIVE);
  assert.ok(row, `[screener] 絞り込み結果に ${POSITIVE} の行がある`);
  const cells = [...row.querySelectorAll("td")].map((td) => td.textContent.trim());
  assert.equal(cells[5], expected, `[screener] ${POSITIVE} の PBR セルが ${expected}`); // 列順: コード,銘柄名,市場,時価総額,PER,PBR,...
  checkNoErrors("screener 画面");
  console.log(`  [OK] screener: ${POSITIVE} PBR列 = ${cells[5]}`);

  // 5) 比較画面: PBR行の追加と算出値
  await goto("#/compare");
  await tick(50);
  document.getElementById("cmp_code").value = POSITIVE;
  document.getElementById("cmp_add").click();
  await tick(150);
  const pbrRow = [...(document.getElementById("cmp_body")?.querySelectorAll("tr") || [])]
    .find((tr) => (tr.querySelector(".metric-name")?.textContent || "").includes("PBR"));
  assert.ok(pbrRow, "[compare] PBR行が存在する");
  const pbrCellText = pbrRow.querySelector("td:not(.metric-name)")?.textContent.trim();
  assert.equal(pbrCellText, expected, `[compare] ${POSITIVE} の PBR が ${expected}`);
  checkNoErrors("compare 画面");
  console.log(`  [OK] compare: ${POSITIVE} PBR行 = ${pbrCellText}`);

  console.log("PBR CALC CHECK PASSED");
  process.exit(0);
}

main().catch((e) => { console.error("PBR CHECK FAILED (harness):", e); process.exit(1); });
