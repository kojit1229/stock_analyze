#!/usr/bin/env node
/**
 * フロントエンド UI スモークテスト (jsdom / ブラウザ不要)
 *
 * 本番 (GitHub Pages) と同じ経路で frontend/app.js を実 DOM 上で走らせ、
 * 次を検証する。1つでも満たさなければ非ゼロ終了する。
 *   - 構文エラーがないこと            … スクリプト評価時に SyntaxError を捕捉
 *   - 全ルートが例外なく描画されること … render() の catch で出る「エラー:」枠を検知
 *   - データが表示されること          … 各ルートで #app に実体があること
 *   - ボタン操作で例外が出ないこと      … #app 内の各ボタンを click してエラー捕捉
 *   - 未捕捉例外 / console.error が無いこと
 *
 * 使い方:  node scripts/smoke_ui.mjs
 */
import { JSDOM } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FE = path.join(ROOT, "frontend");

const errors = [];
const fail = (msg) => errors.push(msg);

const html = fs.readFileSync(path.join(FE, "index.html"), "utf8");
const localApiSrc = fs.readFileSync(path.join(FE, "local-api.js"), "utf8");
const appSrc = fs.readFileSync(path.join(FE, "app.js"), "utf8");

// 本番と同じく GitHub Pages を装う → app.js がローカルモードで起動する
const dom = new JSDOM(html, {
  url: "https://kojit1229.github.io/stock_analyze/frontend/index.html",
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const { window } = dom;
const { document } = window;

// --- 未捕捉エラー / console.error の捕捉 -----------------------------------
window.addEventListener("error", (e) =>
  fail("window error: " + (e.error?.stack || e.message)));
window.addEventListener("unhandledrejection", (e) =>
  fail("unhandledrejection: " + (e.reason?.stack || e.reason)));
window.console.error = (...a) => fail("console.error: " + a.map(String).join(" "));
window.console.warn = () => {};
window.console.log = () => {};

// --- ブラウザ API のスタブ -------------------------------------------------
window.alert = () => {};
window.confirm = () => false;   // 破壊的操作(削除確認)は中断させる
window.prompt = () => null;
window.scrollTo = () => {};
if (!window.matchMedia) window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
window.URL.createObjectURL = () => "blob:stub";
window.URL.revokeObjectURL = () => {};

// --- fetch ポリフィル: data/*.json は disk から。/api/* は 404(=Pagesと同じ) ---
window.fetch = async (input) => {
  const url = String(input);
  const m = url.match(/data\/([^?]+\.json)/);
  if (m) {
    const fp = path.join(FE, "data", m[1]);
    if (fs.existsSync(fp)) {
      return new Response(fs.readFileSync(fp, "utf8"), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("null", { status: 404, headers: { "Content-Type": "application/json" } });
  }
  // 静的ホスティングでは /api/* は存在しない → 非JSONの404 (フォールバック経路)
  return new Response("<html>404</html>", { status: 404, headers: { "Content-Type": "text/html" } });
};

// --- スクリプト評価 (= 構文チェックも兼ねる) --------------------------------
try { window.eval(localApiSrc); }
catch (e) { fail("local-api.js 評価エラー: " + e.stack); }
try { window.eval(appSrc); }
catch (e) { fail("app.js 評価エラー: " + e.stack); }

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const appEl = () => document.getElementById("app");

async function waitRendered(routeName, timeout = 2500) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const app = appEl();
    const txt = app ? app.textContent : "";
    if (app && app.children.length > 0 && !txt.includes("読み込み中")) return;
    await tick(40);
  }
}

function checkRoute(routeName) {
  const app = appEl();
  if (!app) return fail(`[${routeName}] #app が存在しない`);
  const txt = app.textContent || "";
  if (txt.includes("読み込み中")) fail(`[${routeName}] 描画が完了しない (ローディングのまま)`);
  const errBox = app.querySelector(".empty");
  if (errBox && /エラー[:：]/.test(errBox.textContent)) {
    fail(`[${routeName}] 描画エラー: ${errBox.textContent.trim()}`);
  }
  if (app.children.length === 0) fail(`[${routeName}] データが表示されていない (#app が空)`);
}

function closeModals() {
  document.querySelectorAll("[class*='modal']").forEach((m) => {
    // 最外郭のオーバーレイのみ除去
    if (!m.parentElement || !m.parentElement.closest("[class*='modal']")) {
      try { m.remove(); } catch { /* noop */ }
    }
  });
}

async function exerciseButtons(routeName) {
  // 代表的なコントロールを操作し、ハンドラが例外を投げないか確認する。
  // (全ボタン総当たりはしない: 決算予定などは行数分=数千個になり得るため)
  const app = appEl();
  const seen = new Set();
  const targets = [];
  const add = (el) => { if (el && !seen.has(el)) { seen.add(el); targets.push(el); } };

  for (const q of [
    "#applyBtn", "#resetBtn",
    "#dateChips .chip", "#discChips .chip",
    "button[data-reg]", "button[data-edit]", "button[data-del]",
    "th[data-sort]", "#toggleRead", "#saveComment", "#fetchMy",
  ]) add(app.querySelector(q));
  add(document.getElementById("globalFetch")); // ヘッダの「⟳ 取得」
  for (const b of [...app.querySelectorAll("button")].slice(0, 4)) add(b);

  for (const b of targets) {
    if (!b.isConnected) continue;
    const label = b.id || b.getAttribute("data-sort") || (b.textContent || "").trim().slice(0, 10);
    try { b.click(); }
    catch (e) { fail(`[${routeName}] 「${label}」click 例外: ${e.stack || e}`); }
    await tick(15);
    const cancel = document.querySelector(".modal-backdrop #m_cancel");
    if (cancel) { try { cancel.click(); } catch { /* noop */ } }
    closeModals();
  }

  // 分析タブ等の select を1つ変更してみる
  const s = app.querySelector("#an_select") || app.querySelector("select");
  if (s) {
    const opt = [...s.options].find((o) => o.value);
    if (opt) {
      try {
        s.value = opt.value;
        s.dispatchEvent(new window.Event("change", { bubbles: true }));
      } catch (e) { fail(`[${routeName}] select 変更例外: ${e.stack || e}`); }
      await tick(120);
      closeModals();
    }
  }
}

const ROUTES = ["home", "schedule", "mystocks", "disclosures", "analysis", "compare"];

async function main() {
  // 初期描画 (boot の render()) を待つ
  await tick(80);

  let sampleCode = null;
  for (const name of ROUTES) {
    window.location.hash = "#/" + name;
    window.dispatchEvent(new window.Event("hashchange"));
    await waitRendered(name);
    checkRoute(name);
    if (name === "schedule") {
      const reg = appEl().querySelector("button[data-reg]");
      if (reg) sampleCode = reg.getAttribute("data-reg");
    }
    await exerciseButtons(name);
    closeModals();
    await tick(20);
  }

  // 銘柄詳細ルート (ナビには無いが直リンクで到達する)
  if (sampleCode) {
    window.location.hash = "#/stock/" + sampleCode;
    window.dispatchEvent(new window.Event("hashchange"));
    await waitRendered("stock");
    checkRoute("stock/" + sampleCode);
    await exerciseButtons("stock");
    closeModals();
  }

  await tick(50);

  if (errors.length) {
    console.error("UI SMOKE FAILED");
    for (const e of errors) console.error("  - " + e);
    process.exit(1);
  }
  console.log("UI SMOKE PASSED (" + ROUTES.length + " routes)");
  process.exit(0);
}

main().catch((e) => { console.error("UI SMOKE FAILED (harness):", e); process.exit(1); });
