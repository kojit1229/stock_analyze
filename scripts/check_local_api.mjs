#!/usr/bin/env node
/**
 * ローカルAPI (frontend/local-api.js) の回帰チェック。
 *
 * GitHub Pages 用の静的モードがバックエンドと同等に動くことを Node 上で検証する。
 * ブラウザ API (localStorage) が無い環境ではメモリ内フォールバックが使われる。
 *
 *   node scripts/check_local_api.mjs
 */
import assert from "node:assert/strict";

await import("../frontend/local-api.js");
const L = globalThis.LocalApi;
assert.ok(L, "LocalApi がロードされていること");

L._reset();

// 決算予定一覧
const sched = L.handle("GET", "/schedule");
assert.equal(sched.count, 24, "サンプル24銘柄の決算予定");
assert.ok(sched.items[0].market_cap_label, "時価総額ラベルが付与される");

// コード検索
const byCode = L.handle("GET", "/schedule?code=7203");
assert.equal(byCode.count, 1);
assert.equal(byCode.items[0].name, "トヨタ自動車");

// 時価総額レンジ (1兆円以上)
const cho = L.handle("GET", "/schedule?cap_range=gte1cho");
assert.ok(cho.count > 0);
for (const it of cho.items) assert.ok(it.market_cap >= 1e12);

// 任意レンジ (100億〜300億円)
const custom = L.handle("GET", `/schedule?cap_min=${100e8}&cap_max=${300e8}`);
for (const it of custom.items) {
  assert.ok(it.market_cap >= 100e8 && it.market_cap < 300e8);
}

// 並び替え (時価総額 降順)
const sorted = L.handle("GET", "/schedule?sort=cap&order=desc");
const caps = sorted.items.map((i) => i.market_cap);
assert.deepEqual(caps, [...caps].sort((a, b) => b - a));

// マイ銘柄 登録 → 一覧 → 更新 → 削除
assert.throws(() => L.handle("POST", "/mystocks", { code: "0000" }), /存在しません/);
L.handle("POST", "/mystocks", { code: "7203", holding_type: "保有中", importance: 5, memo: "主力" });
let my = L.handle("GET", "/mystocks");
assert.equal(my.count, 1);
assert.equal(my.items[0].holding_type, "保有中");
L.handle("PATCH", "/mystocks/7203", { importance: 2, memo: "更新" });
my = L.handle("GET", "/mystocks");
assert.equal(my.items[0].importance, 2);

// 決算短信の自動取得 (7203 は今日が決算日 → 1件取得) + 重複取得防止
const f1 = L.handle("POST", "/fetch");
assert.ok(f1.fetched >= 1, "登録銘柄の決算短信が取得される");
const f2 = L.handle("POST", "/fetch");
assert.equal(f2.fetched, 0, "重複取得しない");

// 決算短信一覧・閲覧管理・コメント
const discs = L.handle("GET", "/disclosures");
assert.ok(discs.count >= 1);
const id = discs.items[0].id;
const read = L.handle("POST", `/disclosures/${id}/read`, { is_read: true });
assert.equal(read.is_read, 1);
const unread = L.handle("GET", "/disclosures?unread=1");
assert.ok(!unread.items.some((x) => x.id === id), "閲覧済みは未閲覧フィルタから消える");
const commented = L.handle("PATCH", `/disclosures/${id}`, { comment: "好決算" });
assert.equal(commented.comment, "好決算");

// PDF 生成 (Blob URL の元になるバイト列)
const bytes = L.pdfBytes(id);
assert.equal(String.fromCharCode(...bytes.slice(0, 4)), "%PDF", "有効なPDFヘッダ");

// 銘柄詳細・ホーム
const stock = L.handle("GET", "/stocks/7203");
assert.equal(stock.name, "トヨタ自動車");
assert.ok(stock.disclosures.length >= 1);
const home = L.handle("GET", "/home");
for (const k of ["todays_earnings", "unread_disclosures", "fetched_total", "watchlist", "last_updated"]) {
  assert.ok(k in home, `home に ${k} がある`);
}

// マイ銘柄削除
L.handle("DELETE", "/mystocks/7203");
assert.equal(L.handle("GET", "/mystocks").count, 0);

L._reset();
console.log("LOCAL API CHECK PASSED");
