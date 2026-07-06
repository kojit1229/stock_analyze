#!/usr/bin/env node
/**
 * ローカルAPI (frontend/local-api.js) の回帰チェック。
 *
 * GitHub Pages 用の静的モードがバックエンドと同等に動くことを Node 上で検証する。
 * サンプルモードと実データモード (フィクスチャ) の両方をテストする。
 *
 *   node scripts/check_local_api.mjs
 */
import assert from "node:assert/strict";

await import("../frontend/local-api.js");
const L = globalThis.LocalApi;
assert.ok(L, "LocalApi がロードされていること");

// ===========================================================================
// サンプルモード
// ===========================================================================
L._reset();
L._installSample();
assert.equal(L.mode(), "sample");

// 決算予定一覧
const sched = await L.handle("GET", "/schedule");
assert.equal(sched.count, 24, "サンプル24銘柄の決算予定");
assert.ok(sched.items[0].market_cap_label, "時価総額ラベルが付与される");

// コード検索
const byCode = await L.handle("GET", "/schedule?code=7203");
assert.equal(byCode.count, 1);
assert.equal(byCode.items[0].name, "トヨタ自動車");

// 時価総額レンジ (1兆円以上)
const cho = await L.handle("GET", "/schedule?cap_range=gte1cho");
assert.ok(cho.count > 0);
for (const it of cho.items) assert.ok(it.market_cap >= 1e12);

// 任意レンジ (100億〜300億円)
const custom = await L.handle("GET", `/schedule?cap_min=${100e8}&cap_max=${300e8}`);
for (const it of custom.items) {
  assert.ok(it.market_cap >= 100e8 && it.market_cap < 300e8);
}

// 並び替え (時価総額 降順)
const sorted = await L.handle("GET", "/schedule?sort=cap&order=desc");
const caps = sorted.items.map((i) => i.market_cap);
assert.deepEqual(caps, [...caps].sort((a, b) => b - a));

// マイ銘柄 登録 → 一覧 → 更新 → 削除
await assert.rejects(L.handle("POST", "/mystocks", { code: "0000" }), /存在しません/);
await L.handle("POST", "/mystocks", { code: "7203", holding_type: "保有中", importance: 5, memo: "主力" });
let my = await L.handle("GET", "/mystocks");
assert.equal(my.count, 1);
assert.equal(my.items[0].holding_type, "保有中");
await L.handle("PATCH", "/mystocks/7203", { importance: 2, memo: "更新" });
my = await L.handle("GET", "/mystocks");
assert.equal(my.items[0].importance, 2);

// 決算短信の自動取得 (7203 は今日が決算日 → 1件取得) + 重複取得防止
const f1 = await L.handle("POST", "/fetch");
assert.ok(f1.fetched >= 1, "登録銘柄の決算短信が取得される");
const f2 = await L.handle("POST", "/fetch");
assert.equal(f2.fetched, 0, "重複取得しない");

// 決算短信一覧・閲覧管理・コメント
const discs = await L.handle("GET", "/disclosures");
assert.ok(discs.count >= 1);
const id = discs.items[0].id;
const read = await L.handle("POST", `/disclosures/${id}/read`, { is_read: true });
assert.equal(read.is_read, 1);
const unread = await L.handle("GET", "/disclosures?unread=1");
assert.ok(!unread.items.some((x) => x.id === id), "閲覧済みは未閲覧フィルタから消える");
const commented = await L.handle("PATCH", `/disclosures/${id}`, { comment: "好決算" });
assert.equal(commented.comment, "好決算");

// PDF 生成 (Blob URL の元になるバイト列)
const bytes = L.pdfBytes(id);
assert.equal(String.fromCharCode(...bytes.slice(0, 4)), "%PDF", "有効なPDFヘッダ");

// 銘柄詳細・ホーム
const stock = await L.handle("GET", "/stocks/7203");
assert.equal(stock.name, "トヨタ自動車");
assert.ok(stock.disclosures.length >= 1);
const home = await L.handle("GET", "/home");
for (const k of ["todays_earnings", "unread_disclosures", "fetched_total", "watchlist", "last_updated"]) {
  assert.ok(k in home, `home に ${k} がある`);
}

// マイ銘柄削除
await L.handle("DELETE", "/mystocks/7203");
assert.equal((await L.handle("GET", "/mystocks")).count, 0);

console.log("  [OK] sample mode");

// ===========================================================================
// 実データモード (フィクスチャ)
// ===========================================================================
L._reset();
const today = new Date();
const p = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
const todayStr = iso(today);
const nextWeek = iso(new Date(today.getTime() + 7 * 86400e3));

L._install({
  meta: { generated_at: `${todayStr}T09:00:00` },
  stocks: [
    { code: "7203", name: "トヨタ自動車", market: "プライム", sector: "輸送用機器", market_cap: 40e12 },
    { code: "6758", name: "ソニーグループ", market: "プライム", sector: "電気機器", market_cap: null },
  ],
  schedule: [
    { code: "7203", date: nextWeek, fiscal_type: "第1四半期" },
    { code: "6758", date: todayStr, fiscal_type: "本決算" },
  ],
  disclosures: [
    {
      key: "td-001", code: "7203",
      title: "2026年3月期 第1四半期決算短信〔日本基準〕(連結)",
      pdf_url: "https://www.release.tdnet.info/inbs/sample.pdf",
      doc_type: "決算短信", published_at: `${todayStr}T15:00:00`,
    },
  ],
});
assert.equal(L.mode(), "real");

// 実データの決算予定・null時価総額の扱い
const rs = await L.handle("GET", "/schedule");
assert.equal(rs.count, 2);
const sony = rs.items.find((x) => x.code === "6758");
assert.equal(sony.market_cap_label, "-", "時価総額なしは '-' 表示");
const capFiltered = await L.handle("GET", "/schedule?cap_range=gte1cho");
assert.equal(capFiltered.count, 1, "時価総額なしはレンジ絞り込みから除外");

// 実データの決算短信: 外部PDF URL・閲覧オーバーレイ
const rd = await L.handle("GET", "/disclosures");
assert.equal(rd.count, 1);
assert.equal(rd.items[0].is_read, 0);
const rid = rd.items[0].id;
assert.match(L.pdfBlobUrl(rid), /^https:\/\/www\.release\.tdnet\.info\//, "実PDFは外部URL");
await L.handle("POST", `/disclosures/${rid}/read`, { is_read: true });
const rd2 = await L.handle("GET", `/disclosures/${rid}`);
assert.equal(rd2.is_read, 1, "閲覧済みがオーバーレイに保存される");
await L.handle("PATCH", `/disclosures/${rid}`, { comment: "実データメモ" });
assert.equal((await L.handle("GET", `/disclosures/${rid}`)).comment, "実データメモ");

// マイ銘柄のみフィルタ
await L.handle("POST", "/mystocks", { code: "6758" });
const mineOnly = await L.handle("GET", "/disclosures?mine=1");
assert.equal(mineOnly.count, 0, "登録していない銘柄の開示は mine=1 で出ない");
await L.handle("POST", "/mystocks", { code: "7203" });
assert.equal((await L.handle("GET", "/disclosures?mine=1")).count, 1);

// 実データモードのホーム: 未確認カウントは登録銘柄スコープ
const rhome = await L.handle("GET", "/home");
assert.equal(rhome.data_mode, "real");
assert.equal(rhome.fetched_total, 1);
assert.equal(rhome.unread_disclosures, 0, "閲覧済みにしたので未確認0");
assert.equal(rhome.last_updated.schedule, `${todayStr}T09:00:00`);

console.log("  [OK] real mode (fixtures)");

L._reset();
console.log("LOCAL API CHECK PASSED");
