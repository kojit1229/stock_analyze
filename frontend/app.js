"use strict";
/* 決算ナビ フロントエンド (依存ライブラリなしのバニラ JS SPA) */

// ---------------------------------------------------------------------------
// API クライアント
// ---------------------------------------------------------------------------
const api = {
  // local=true のとき、サーバの代わりに LocalApi (local-api.js) で処理する。
  // GitHub Pages などの静的ホスティングでは /api/* が存在しないため、
  // 初回失敗時に自動でローカルモードへフォールバックする。
  local: false,
  async req(method, path, body) {
    if (this.local && window.LocalApi) {
      const result = await window.LocalApi.handle(method, path, body);
      markLocalMode();
      return result;
    }
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    try {
      const res = await fetch("/api" + path, opt);
      const text = await res.text();
      let data = null;
      if (text) {
        try { data = JSON.parse(text); }
        catch (e) {
          // JSON でない応答 (静的ホスティングの404ページ等) → フォールバック対象
          const err = new Error("APIが見つかりません");
          err.fallback = true;
          throw err;
        }
      }
      if (!res.ok) throw new Error((data && data.error) || res.statusText);
      return data;
    } catch (e) {
      if (window.LocalApi && (e.fallback || e instanceof TypeError)) {
        this.local = true;
        const result = await window.LocalApi.handle(method, path, body);
        markLocalMode();
        return result;
      }
      throw e;
    }
  },
  // PDF の表示/ダウンロード用 URL (ローカルモードでは Blob URL を生成)
  pdfUrl(id) {
    return this.local && window.LocalApi
      ? window.LocalApi.pdfBlobUrl(id)
      : `/api/disclosures/${id}/pdf`;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b || {}); },
  patch(p, b) { return this.req("PATCH", p, b || {}); },
  del(p) { return this.req("DELETE", p); },
};

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------
const el = (id) => document.getElementById(id);
const h = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));

function toast(msg, isError) {
  const t = el("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  setTimeout(() => (t.className = "toast"), 2600);
}

// 静的モード (GitHub Pages 等) のインジケータ。
// LocalApi のデータモードに応じて「実データ」または「デモ」を表示する。
let _localBadgeMode = null;
function markLocalMode() {
  const mode = window.LocalApi && window.LocalApi.mode ? window.LocalApi.mode() : "sample";
  if (_localBadgeMode === mode) return;
  _localBadgeMode = mode;
  let badge = document.getElementById("localModeBadge");
  if (!badge) {
    const brand = document.querySelector(".brand-name");
    if (!brand) return;
    brand.insertAdjacentHTML("afterend", '<span class="badge market" id="localModeBadge"></span>');
    badge = document.getElementById("localModeBadge");
  }
  if (mode === "real") {
    badge.textContent = "実データ";
    badge.title = "JPX・TDnetの公開データ (GitHub Actionsが定期取得)。マイ銘柄などはこのブラウザに保存されます";
    badge.className = "badge ok";
    badge.id = "localModeBadge";
  } else {
    badge.textContent = "デモ";
    badge.title = "サーバなしで動作中。サンプルデータとブラウザ生成PDFを使用します";
  }
}

function fmtDate(d) {
  if (!d) return "-";
  const dt = new Date(d.length <= 10 ? d + "T00:00:00" : d);
  if (isNaN(dt)) return d;
  const w = ["日", "月", "火", "水", "木", "金", "土"][dt.getDay()];
  return `${dt.getMonth() + 1}/${dt.getDate()}(${w})`;
}
function fmtDateTime(d) {
  if (!d) return "-";
  const dt = new Date(d);
  if (isNaN(dt)) return d;
  return `${dt.getFullYear()}/${dt.getMonth() + 1}/${dt.getDate()} ${String(dt.getHours()).padStart(2, "0")}:${String(dt.getMinutes()).padStart(2, "0")}`;
}
function stars(n) {
  n = Math.max(0, Math.min(5, n || 0));
  return "★".repeat(n) + "☆".repeat(5 - n);
}
function statusBadge(status) {
  return status === "取得済み"
    ? '<span class="badge ok">取得済み</span>'
    : '<span class="badge pending">未取得</span>';
}
function regBadge(isReg) {
  return isReg ? '<span class="badge reg">登録済み</span>' : "";
}

const HOLDING_TYPES = ["保有中", "監視中", "売却済み", "気になる銘柄"];

// ---------------------------------------------------------------------------
// ルーター
// ---------------------------------------------------------------------------
const routes = {};
function route(name, fn) { routes[name] = fn; }

async function render() {
  const hash = location.hash.replace(/^#\/?/, "") || "home";
  const [name, ...rest] = hash.split("/");
  const app = el("app");
  // ナビのアクティブ表示
  document.querySelectorAll(".mainnav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.route === name));
  app.innerHTML = '<div class="loading">読み込み中…</div>';
  const fn = routes[name] || routes.home;
  try {
    await fn(app, rest);
  } catch (e) {
    app.innerHTML = `<div class="empty">エラー: ${h(e.message)}</div>`;
  }
  // データ変更の可能性があるため、画面遷移のたびに自動バックアップを予約
  // (有効時のみ。ハッシュ不変ならpushしない)
  scheduleAutoBackup();
}
window.addEventListener("hashchange", render);

// ---------------------------------------------------------------------------
// ホーム画面
// ---------------------------------------------------------------------------
route("home", async (app) => {
  const [d, alertData, myList, pricesData] = await Promise.all([
    api.get("/home"), loadAlerts(), api.get("/mystocks"), loadPrices(),
  ]);
  const upcoming = d.registered_upcoming || [];
  const watch = d.watchlist || [];
  // マイ銘柄の本日サマリ
  let mySummary = "";
  {
    const chgs = (myList.items || [])
      .map((m) => pricesData.stocks[m.code])
      .filter((p) => p && p[1] != null)
      .map((p) => p[1]);
    if (chgs.length) {
      const avg = chgs.reduce((s, v) => s + v, 0) / chgs.length;
      const u = chgs.filter((v) => v > 0.0001).length;
      const dn = chgs.filter((v) => v < -0.0001).length;
      mySummary = `<div class="meta-line" style="margin-bottom:8px">マイ銘柄の本日 (${chgs.length}銘柄): 平均
        <span style="color:${avg >= 0 ? "#4ade80" : "#f87171"};font-weight:700">${avg >= 0 ? "+" : ""}${avg.toFixed(2)}%</span>
        (上昇 ${u} / 下落 ${dn} / 変わらず ${chgs.length - u - dn}) — 市場全体は<a class="link" href="#/market">市況タブ</a>へ</div>`;
    }
  }
  // 直近7日のアラート
  const ct = new Date();
  ct.setDate(ct.getDate() - 7);
  const pd = (n) => String(n).padStart(2, "0");
  const cutoff = `${ct.getFullYear()}-${pd(ct.getMonth() + 1)}-${pd(ct.getDate())}`;
  const alerts = (alertData.alerts || []).filter((a) => (a.date || "") >= cutoff).slice(0, 30);
  app.innerHTML = `
    <div class="page-head">
      <h1>ホーム</h1>
      <span class="sub">${fmtDate(d.date)} 時点</span>
    </div>
    <div class="grid cols-4" style="margin-bottom:16px">
      <div class="card stat"><div class="label">今日の決算予定</div><div class="value accent">${d.todays_count}</div></div>
      <div class="card stat"><div class="label">未確認の決算短信</div><div class="value warn">${d.unread_disclosures}</div></div>
      <div class="card stat"><div class="label">取得済み決算短信</div><div class="value ok">${d.fetched_total}</div></div>
      <div class="card stat"><div class="label">アラート (7日間)</div><div class="value ${alerts.length ? "warn" : ""}">${alerts.length}</div></div>
    </div>
    <div class="card" style="margin-bottom:16px">
      <h2>🔔 アラート <span class="count">直近7日 ${alerts.length}件</span>
        <a class="link" href="#/settings" style="margin-left:auto;font-size:12px">⚙ アラート設定 →</a></h2>
      ${mySummary}
      ${alerts.length ? `<div class="table-wrap"><table>
        <thead><tr><th>日付</th><th>コード</th><th>銘柄名</th><th>重要度</th><th>アラート</th><th>詳細</th></tr></thead>
        <tbody>${alerts.map((a) => `<tr>
          <td>${fmtDate(a.date)}</td>
          <td class="code-cell"><a class="link" href="#/analysis/${h(a.code)}">${h(a.code)}</a></td>
          <td>${h(a.name || "")}</td>
          <td><span class="star">${stars(a.importance)}</span></td>
          <td>${alertIcon(a.type)} ${h(a.title || "")}</td>
          <td class="num">${h(a.detail || "")}</td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty">アラートはありません。マイ銘柄の終値アラート (前日比変動・52週高安・出来高急増・決算前日/当日・重要開示・3日連続続落/続伸・決算への反応) は
        <a class="link" href="#/settings">⚙ 設定</a>でgitへの自動バックアップを有効にすると、平日の引け後に自動チェックされます。</div>`}
    </div>
    <div class="grid cols-2">
      <div class="card">
        <h2>📅 今日の決算予定 <span class="count">${d.todays_count}件</span></h2>
        ${scheduleMiniTable(d.todays_earnings)}
      </div>
      <div class="card">
        <h2>⭐ 登録銘柄の直近決算 <span class="count">${upcoming.length}件</span></h2>
        ${upcoming.length ? `<div class="table-wrap"><table><thead><tr><th>予定日</th><th>コード</th><th>銘柄名</th><th>種別</th></tr></thead><tbody>
          ${upcoming.map((s) => `<tr>
            <td>${fmtDate(s.announce_date)}</td>
            <td class="code-cell"><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a></td>
            <td>${h(s.name)}</td>
            <td>${h(s.fiscal_type || "")}</td></tr>`).join("")}
        </tbody></table></div>` : `<div class="empty">登録銘柄がありません。<a class="link" href="#/schedule">決算予定</a>から登録できます。</div>`}
      </div>
    </div>
    <div class="grid cols-2" style="margin-top:16px">
      <div class="card">
        <h2>🔥 注目銘柄</h2>
        ${watch.length ? watch.map((s) => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
          <span><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a> ${h(s.name)}</span>
          <span class="star">${stars(s.importance)}</span></div>`).join("") : '<div class="empty">注目銘柄は未登録です</div>'}
      </div>
      <div class="card">
        <h2>ℹ️ データ最終更新</h2>
        <dl class="kv">
          <dt>決算日程</dt><dd>${fmtDateTime(d.last_updated.schedule)}</dd>
          <dt>決算短信</dt><dd>${fmtDateTime(d.last_updated.disclosure)}</dd>
        </dl>
        <div class="meta-line">「⟳ 取得」で登録銘柄の決算短信を取得します。</div>
      </div>
    </div>`;
});

function scheduleMiniTable(items) {
  if (!items || !items.length) return '<div class="empty">本日の決算予定はありません</div>';
  return `<div class="table-wrap"><table><thead><tr><th>コード</th><th>銘柄名</th><th>時価総額</th><th>取得</th></tr></thead><tbody>
    ${items.map((s) => `<tr>
      <td class="code-cell"><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a></td>
      <td>${h(s.name)} ${regBadge(s.is_registered)}</td>
      <td class="num">${h(s.market_cap_label)}</td>
      <td>${statusBadge(s.fetch_status)}</td></tr>`).join("")}
  </tbody></table></div>`;
}

// ---------------------------------------------------------------------------
// 決算予定一覧画面（検索・絞り込み・並び替え）
// ---------------------------------------------------------------------------
const scheduleState = {
  date_range: "all", code: "", name: "", sector: "", market: "",
  cap_range: "", cap_min: "", cap_max: "", sort: "date", order: "asc",
  mine: false, imp: "",
};

route("schedule", async (app) => {
  const [caps, sectors, markets] = await Promise.all([
    api.get("/cap-ranges"), api.get("/sectors"), api.get("/markets"),
  ]);
  app.innerHTML = `
    <div class="page-head"><h1>決算予定一覧</h1><span class="sub">条件で絞り込み・並び替えができます</span></div>
    <div class="chips" id="dateChips">
      ${[["all", "すべて"], ["today", "今日"], ["tomorrow", "明日"], ["this_week", "今週"], ["next_week", "来週"], ["month", "1ヶ月"]]
        .map(([k, l]) => `<span class="chip ${scheduleState.date_range === k ? "active" : ""}" data-range="${k}">${l}</span>`).join("")}
      <span style="width:1px;background:var(--border);align-self:stretch;margin:0 4px"></span>
      <span class="chip ${scheduleState.mine ? "active" : ""}" id="f_mine">⭐ マイ銘柄のみ</span>
      <select id="f_imp" style="width:auto" title="マイ銘柄の重要度で絞り込み (未登録の銘柄は除外されます)">
        <option value="">重要度: すべて</option>
        ${[["5", "★5のみ"], ["4", "★4以上"], ["3", "★3以上"], ["2", "★2以上"], ["1", "★1以上"]]
          .map(([k, l]) => `<option value="${k}" ${scheduleState.imp === k ? "selected" : ""}>${l}</option>`).join("")}
      </select>
    </div>
    <div class="filters">
      <div class="field"><label>銘柄コード</label><input id="f_code" value="${h(scheduleState.code)}" placeholder="例: 7203"></div>
      <div class="field"><label>銘柄名</label><input id="f_name" value="${h(scheduleState.name)}" placeholder="例: トヨタ"></div>
      <div class="field"><label>市場区分</label><select id="f_market"><option value="">すべて</option>
        ${markets.markets.map((m) => `<option ${scheduleState.market === m ? "selected" : ""}>${h(m)}</option>`).join("")}</select></div>
      <div class="field"><label>業種</label><select id="f_sector"><option value="">すべて</option>
        ${sectors.sectors.map((s) => `<option ${scheduleState.sector === s ? "selected" : ""}>${h(s)}</option>`).join("")}</select></div>
      <div class="field"><label>時価総額レンジ</label><select id="f_cap"><option value="">すべて</option>
        ${caps.ranges.map((r) => `<option value="${r.key}" ${scheduleState.cap_range === r.key ? "selected" : ""}>${h(r.label)}</option>`).join("")}</select></div>
      <div class="field"><label>任意レンジ 下限(億円)</label><input id="f_capmin" type="number" value="${scheduleState.cap_min}" placeholder="例: 100"></div>
      <div class="field"><label>任意レンジ 上限(億円)</label><input id="f_capmax" type="number" value="${scheduleState.cap_max}" placeholder="例: 3000"></div>
      <div class="field" style="justify-content:flex-end"><label>&nbsp;</label>
        <div style="display:flex;gap:6px"><button class="btn" id="applyBtn">検索</button><button class="btn ghost" id="resetBtn">クリア</button></div></div>
    </div>
    <div id="scheduleResult"></div>`;

  el("dateChips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    if (c.id === "f_mine") {
      scheduleState.mine = !scheduleState.mine;
      c.classList.toggle("active", scheduleState.mine);
      loadSchedule();
      return;
    }
    if (!c.dataset.range) return;
    scheduleState.date_range = c.dataset.range;
    document.querySelectorAll("#dateChips .chip[data-range]").forEach((x) => x.classList.toggle("active", x === c));
    loadSchedule();
  });
  el("f_imp").onchange = () => {
    scheduleState.imp = el("f_imp").value;
    loadSchedule();
  };
  el("applyBtn").onclick = () => {
    scheduleState.code = el("f_code").value.trim();
    scheduleState.name = el("f_name").value.trim();
    scheduleState.market = el("f_market").value;
    scheduleState.sector = el("f_sector").value;
    scheduleState.cap_range = el("f_cap").value;
    scheduleState.cap_min = el("f_capmin").value;
    scheduleState.cap_max = el("f_capmax").value;
    loadSchedule();
  };
  el("resetBtn").onclick = () => {
    Object.assign(scheduleState, { code: "", name: "", sector: "", market: "", cap_range: "", cap_min: "", cap_max: "", mine: false, imp: "" });
    render();
  };
  await loadSchedule();
});

function capParam(v) { return v === "" || v == null ? "" : String(Number(v) * 1e8); } // 億円→円

async function loadSchedule() {
  const box = el("scheduleResult");
  if (box) box.innerHTML = '<div class="loading">検索中…</div>';
  const q = new URLSearchParams();
  q.set("date_range", scheduleState.date_range);
  q.set("sort", scheduleState.sort);
  q.set("order", scheduleState.order);
  for (const k of ["code", "name", "sector", "market", "cap_range"]) {
    if (scheduleState[k]) q.set(k, scheduleState[k]);
  }
  if (scheduleState.cap_min !== "") q.set("cap_min", capParam(scheduleState.cap_min));
  if (scheduleState.cap_max !== "") q.set("cap_max", capParam(scheduleState.cap_max));
  const d = await api.get("/schedule?" + q.toString());
  // マイ銘柄・重要度での絞り込み (サーバ/静的の両モード共通のクライアント側処理)
  if (scheduleState.mine || scheduleState.imp) {
    const my = await api.get("/mystocks");
    const impBy = new Map((my.items || []).map((m) => [m.code, m.importance || 0]));
    d.items = d.items.filter((s) => impBy.has(s.code) &&
      (!scheduleState.imp || impBy.get(s.code) >= Number(scheduleState.imp)));
    d.count = d.items.length;
  }
  if (!box) return;
  const arrow = (col) => scheduleState.sort === col ? (scheduleState.order === "asc" ? " ▲" : " ▼") : "";
  box.innerHTML = `
    <div class="page-head"><span class="sub">${d.count}件ヒット${scheduleState.mine || scheduleState.imp ? " (マイ銘柄で絞り込み中)" : ""}</span></div>
    ${d.count === 0 ? '<div class="empty">条件に一致する決算予定はありません</div>' : `
    <div class="table-wrap"><table>
      <thead><tr>
        <th data-sort="date">予定日${arrow("date")}</th>
        <th data-sort="code">コード${arrow("code")}</th>
        <th data-sort="name">銘柄名${arrow("name")}</th>
        <th class="no-sort">市場</th>
        <th class="no-sort">業種</th>
        <th data-sort="cap">時価総額${arrow("cap")}</th>
        <th class="no-sort">種別</th>
        <th class="no-sort">取得</th>
        <th class="no-sort">操作</th>
      </tr></thead>
      <tbody>${d.items.map(scheduleRow).join("")}</tbody>
    </table></div>`}`;
  box.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const col = th.dataset.sort;
      if (scheduleState.sort === col) scheduleState.order = scheduleState.order === "asc" ? "desc" : "asc";
      else { scheduleState.sort = col; scheduleState.order = "asc"; }
      loadSchedule();
    };
  });
  box.querySelectorAll("button[data-reg]").forEach((b) => {
    b.onclick = () => openRegisterModal(b.dataset.reg, b.dataset.name);
  });
}

function scheduleRow(s) {
  return `<tr>
    <td>${fmtDate(s.announce_date)}${s.announce_time ? ` <span class="badge market">${h(s.announce_time)}</span>` : ""}</td>
    <td class="code-cell"><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a></td>
    <td>${h(s.name)} ${regBadge(s.is_registered)}</td>
    <td><span class="badge market">${h(s.market || "")}</span></td>
    <td>${h(s.sector || "")}</td>
    <td class="num">${h(s.market_cap_label)}</td>
    <td>${h(s.fiscal_type || "")}</td>
    <td>${statusBadge(s.fetch_status)}</td>
    <td class="row-actions">${s.is_registered
      ? '<span class="badge reg">登録済み</span>'
      : `<button class="btn small" data-reg="${h(s.code)}" data-name="${h(s.name)}">＋登録</button>`}</td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// マイ銘柄画面
// ---------------------------------------------------------------------------
route("mystocks", async (app) => {
  const [d, prices] = await Promise.all([api.get("/mystocks"), loadPrices()]);
  const items = myState.imp
    ? d.items.filter((s) => (s.importance || 0) >= Number(myState.imp))
    : d.items;
  const impChip = (k, l) =>
    `<span class="chip ${myState.imp === k ? "active" : ""}" data-imp="${k}">${l}</span>`;
  app.innerHTML = `
    <div class="page-head"><h1>マイ銘柄</h1><span class="sub">${d.count}件登録中${myState.imp ? ` / 表示${items.length}件` : ""}</span>
      <span style="margin-left:auto;display:inline-flex;gap:8px;align-items:center">
        <button class="btn small ghost" id="backupBtn" title="マイ銘柄・閲覧状態・分析コメント等をJSONファイルに保存">⬇ バックアップ</button>
        <button class="btn small ghost" id="restoreBtn" title="バックアップJSONから復元">⬆ 復元</button>
        <input type="file" id="restoreFile" accept=".json,application/json" style="display:none">
        <button class="fetch-btn" id="fetchMy">⟳ 決算短信を取得</button>
      </span></div>
    <div class="chips" id="impChips">
      ${impChip("", "重要度: すべて")}${impChip("5", "★5のみ")}${impChip("4", "★4以上")}${impChip("3", "★3以上")}${impChip("2", "★2以上")}
    </div>
    <div class="meta-line" style="margin-bottom:12px">マイ銘柄・閲覧状態・分析コメントはこのブラウザにのみ保存されます。<a class="link" href="#/settings">⚙ 設定</a>からgitへの自動バックアップを有効にすると、端末を変えても復元でき、終値アラートのメール通知も使えます。</div>
    ${items.length === 0 ? `<div class="empty">${d.count === 0 ? 'まだ銘柄が登録されていません。<a class="link" href="#/schedule">決算予定一覧</a>から登録しましょう。' : "この重要度のマイ銘柄はありません。"}</div>` : `
    <div class="table-wrap"><table>
      <thead><tr><th>コード</th><th>銘柄名</th><th>保有区分</th><th>重要度</th><th>終値${prices.date ? `<span class="badge market" style="margin-left:4px">${h(fmtDate(prices.date))}</span>` : ""}</th><th>前日比</th><th>次回決算</th><th>取得状況</th><th>メモ</th><th>操作</th></tr></thead>
      <tbody>${items.map((s) => myStockRow(s, prices.stocks[s.code])).join("")}</tbody>
    </table></div>`}`;
  el("impChips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    myState.imp = c.dataset.imp;
    render();
  });
  const fm = el("fetchMy");
  if (fm) fm.onclick = () => runFetch();
  el("backupBtn").onclick = downloadBackup;
  el("restoreBtn").onclick = () => el("restoreFile").click();
  el("restoreFile").onchange = () => {
    const f = el("restoreFile").files && el("restoreFile").files[0];
    if (f) restoreBackup(f);
    el("restoreFile").value = "";
  };
  app.querySelectorAll("button[data-edit]").forEach((b) => b.onclick = () => openEditModal(b.dataset.edit));
  app.querySelectorAll("button[data-del]").forEach((b) => b.onclick = () => removeMyStock(b.dataset.del));
});

const myState = { imp: "" }; // マイ銘柄タブの重要度フィルタ (★n以上)

function myStockRow(s, price) {
  const unread = s.unread_count ? ` <span class="badge unread">未読${s.unread_count}</span>` : "";
  const close = price && price[0] != null
    ? Number(price[0]).toLocaleString("en-US", { maximumFractionDigits: 1 }) + "円" : "-";
  const chg = price && price[1] != null
    ? `<span class="${price[1] >= 0 ? "pos" : "neg"}" style="color:${price[1] >= 0 ? "#4ade80" : "#f87171"}">${price[1] >= 0 ? "+" : ""}${Number(price[1]).toFixed(2)}%</span>` : "-";
  return `<tr>
    <td class="code-cell"><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a></td>
    <td>${h(s.name)}</td>
    <td><span class="badge market">${h(s.holding_type || "")}</span></td>
    <td><span class="star">${stars(s.importance)}</span></td>
    <td class="num">${close}</td>
    <td class="num">${chg}</td>
    <td>${s.next_announce_date ? fmtDate(s.next_announce_date) + " " + h(s.next_fiscal_type || "") : "-"}</td>
    <td>${statusBadge(s.fetch_status)}${unread}</td>
    <td>${h(s.memo || "")}</td>
    <td class="row-actions">
      <button class="btn small ghost" data-edit="${h(s.code)}">編集</button>
      <button class="btn small danger" data-del="${h(s.code)}">削除</button>
    </td></tr>`;
}

async function removeMyStock(code) {
  if (!confirm(`${code} をマイ銘柄から削除しますか?`)) return;
  await api.del("/mystocks/" + code);
  toast("削除しました");
  render();
}

// ---------------------------------------------------------------------------
// 決算短信一覧画面
// ---------------------------------------------------------------------------
const disclosureState = { filter: "all", code: "", cap: "" }; // filter: all | mine | unread

route("disclosures", async (app, rest) => {
  if (rest && rest.length) return disclosureDetail(app, rest[0]);
  const qp = new URLSearchParams();
  if (disclosureState.filter === "unread") qp.set("unread", "1");
  if (disclosureState.filter === "mine") qp.set("mine", "1");
  if (disclosureState.cap) qp.set("cap_range", disclosureState.cap);
  const qs = qp.toString();
  const [d, caps, my] = await Promise.all([
    api.get("/disclosures" + (qs ? "?" + qs : "")),
    api.get("/cap-ranges"),
    api.get("/mystocks"),
  ]);
  const chip = (key, label) =>
    `<span class="chip ${disclosureState.filter === key ? "active" : ""}" data-filter="${key}">${label}</span>`;
  const checkboxes = my.items.map((m) =>
    `<label><input type="checkbox" value="${h(m.code)}" checked> ${h(m.code)} ${h(m.name)}</label>`).join("");
  app.innerHTML = `
    <div class="page-head"><h1>決算短信</h1><span class="sub">${d.count}件</span></div>
    <div class="card" style="margin-bottom:14px">
      <h2>📥 決算短信のまとめて取得・GitHubへのPDF保存</h2>
      ${my.count ? `
        <div class="meta-line">対象のマイ銘柄を選んで実行します。</div>
        <div class="checklist" id="disc_checklist">${checkboxes}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="disc_bulk" title="開示アーカイブから過去2年分の決算短信・修正・説明資料の履歴を取得し、銘柄分析タブに表示します">📄 履歴をまとめて取得 (2年分)</button>
          <button class="btn" id="disc_pdf_save" title="選択した銘柄をPDF恒久保存リストに追加し、GitHub Actionsが決算短信PDF本体をリポジトリへ保存します">💾 決算短信PDFをGitHubに保存</button>
        </div>
        <div class="fetch-status" id="disc_bulk_status"></div>
        <div class="meta-line">「GitHubに保存」は銘柄を<b>PDF恒久保存リスト</b>に追加し、GitHub Actionsが決算短信PDF本体をリポジトリ(frontend/pdfs/)へ保存します。以後の決算短信は自動で保存され続け、TDnetの掲載期間(約1ヶ月)を過ぎても閲覧できます。実行には⚙設定のGitHubトークンが必要です。過去2年分の「履歴取得」の結果はこのブラウザに保存されます。</div>
      ` : '<div class="empty">マイ銘柄が未登録です。<a class="link" href="#/schedule">決算予定</a>から登録すると、まとめて取得・保存できます。</div>'}
      <div style="margin-top:10px">
        <span style="font-size:12px;font-weight:700">📌 PDF恒久保存リスト <span class="count" id="pdf_wl_count"></span></span>
        <div id="pdf_wl_codes" style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">読み込み中…</div>
      </div>
    </div>
    <div class="chips" id="discChips" style="align-items:center">
      ${chip("all", "すべて")}${chip("mine", "マイ銘柄のみ")}${chip("unread", "未閲覧のみ")}
      <select id="disc_cap" style="width:auto;margin-left:6px">
        <option value="">時価総額: すべて</option>
        ${caps.ranges.map((r) => `<option value="${r.key}" ${disclosureState.cap === r.key ? "selected" : ""}>${h(r.label)}</option>`).join("")}
      </select>
    </div>
    ${d.count === 0 ? '<div class="empty">該当する決算短信がありません。条件を変更するか、マイ銘柄を登録して「⟳ 取得」を実行してください。</div>' : `
    <div class="table-wrap"><table>
      <thead><tr><th>状態</th><th>コード</th><th>銘柄名</th><th>時価総額</th><th>種別</th><th>タイトル</th><th>公開日時</th><th>取得日時</th><th></th></tr></thead>
      <tbody>${d.items.map(disclosureRow).join("")}</tbody>
    </table></div>`}
    <div class="meta-line" style="margin-top:10px">一覧のデータはGitHub Actionsが定期取得しリポジトリ(frontend/data/)に格納したものです。PDF本体はアプリには保存せず、TDnetのPDFを直接開きます(過去2年分の履歴は銘柄分析タブから)。閲覧状態・コメントはこのブラウザに保存されます。</div>`;
  el("discChips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    disclosureState.filter = c.dataset.filter;
    render();
  });
  el("disc_cap").onchange = () => {
    disclosureState.cap = el("disc_cap").value;
    render();
  };

  renderPdfWatchlist();
  const selectedCodes = () =>
    [...document.querySelectorAll("#disc_checklist input:checked")].map((x) => x.value);
  const bulkBtn = el("disc_bulk");
  if (bulkBtn) {
    bulkBtn.onclick = async () => {
      const codes = selectedCodes();
      if (!codes.length) { toast("銘柄を選択してください", true); return; }
      bulkBtn.disabled = true;
      try {
        const res = await bulkFetchHistory(codes, el("disc_bulk_status"));
        const st = el("disc_bulk_status");
        if (st && res && res.message) st.textContent = res.message + " 結果は銘柄分析タブで確認できます。";
      } finally {
        bulkBtn.disabled = false;
      }
    };
  }
  const saveBtn = el("disc_pdf_save");
  if (saveBtn) {
    saveBtn.onclick = async () => {
      const codes = selectedCodes();
      if (!codes.length) { toast("銘柄を選択してください", true); return; }
      saveBtn.disabled = true;
      const st = el("disc_bulk_status");
      try {
        const msg = await savePdfWatchlist(codes, st);
        if (st) st.textContent = msg;
        renderPdfWatchlist();
      } catch (e) {
        toast(e.message, true);
        if (st) st.textContent = "保存に失敗しました: " + e.message;
      } finally {
        saveBtn.disabled = false;
      }
    };
  }
});

// PDF恒久保存リストの表示 (config はリポジトリ直下 → 静的配信では ../config/)
async function renderPdfWatchlist() {
  const box = el("pdf_wl_codes");
  if (!box) return;
  let codes = [];
  try {
    const res = await fetch("../config/pdf_watchlist.json", { cache: "no-cache" });
    if (res.ok) {
      const w = await res.json();
      if (w && Array.isArray(w.codes)) codes = w.codes;
    }
  } catch (e) { /* サーバモード等では取得不可 */ }
  const idx = await loadPdfIndex();
  if (!box.isConnected) return;
  if (!codes.length) {
    box.innerHTML = '<span class="meta-line">リストは空です (「GitHubに保存」で追加できます)</span>';
    return;
  }
  const cnt = el("pdf_wl_count");
  if (cnt) cnt.textContent = `${codes.length}銘柄`;
  box.innerHTML = codes.map((c) => {
    const saved = ((idx.codes || {})[c] || []).length;
    return `<span class="badge reg" style="font-size:12px;padding:4px 10px">${h(c)}${saved ? ` (${saved}件保存済)` : ""}</span>`;
  }).join(" ");
}

// 選択銘柄を config/pdf_watchlist.json に追記コミットし、データ更新ワークフローを起動する
async function savePdfWatchlist(codes, statusEl) {
  const token = ghToken();
  if (!token) {
    throw new Error("GitHubトークンが未設定です。⚙設定画面で登録してください");
  }
  const { owner, repo } = repoInfo();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/config/pdf_watchlist.json`;
  const headers = { Authorization: "Bearer " + token, Accept: "application/vnd.github+json" };
  if (statusEl) statusEl.textContent = "保存リストを更新中…";
  // 現在のリストを取得してマージ
  let sha, wl = { note: "決算短信PDFを恒久保存する銘柄リスト", max_recommended: 50, codes: [] };
  const g = await fetch(url + "?ref=main", { headers });
  if (g.ok) {
    const j = await g.json();
    sha = j.sha;
    try {
      const cur = JSON.parse(decodeURIComponent(escape(atob((j.content || "").replace(/\n/g, "")))));
      if (cur && typeof cur === "object") wl = Object.assign(wl, cur);
      if (!Array.isArray(wl.codes)) wl.codes = [];
    } catch (e) { /* 壊れていたら作り直す */ }
  } else if (g.status !== 404) {
    throw new Error("GitHub API " + g.status + " (トークンの権限を確認してください)");
  }
  const before = wl.codes.length;
  for (const c of codes) {
    if (!wl.codes.includes(c)) wl.codes.push(c);
  }
  const added = wl.codes.length - before;
  if (wl.codes.length > (wl.max_recommended || 50)) {
    toast(`保存リストが${wl.codes.length}銘柄になりました (推奨は${wl.max_recommended || 50}銘柄まで)`, true);
  }
  if (added > 0) {
    const body = JSON.stringify(wl, null, 1);
    const put = await fetch(url, {
      method: "PUT",
      headers,
      body: JSON.stringify({
        message: `PDF保存リストに${added}銘柄を追加`,
        content: btoa(unescape(encodeURIComponent(body))),
        branch: "main",
        sha,
      }),
    });
    if (!put.ok) {
      const err = await put.json().catch(() => ({}));
      throw new Error(err.message || ("HTTP " + put.status));
    }
  }
  // データ更新ワークフローを起動 (Actions権限が無いトークンでは403 → 定期実行に任せる)
  if (statusEl) statusEl.textContent = "保存ワークフローを起動中…";
  const disp = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/update-data.yml/dispatches`,
    { method: "POST", headers, body: JSON.stringify({ ref: "main" }) });
  const listMsg = added > 0 ? `${added}銘柄を保存リストに追加しました。` : "選択銘柄は既に保存リストに登録済みです。";
  if (disp.ok || disp.status === 204) {
    return listMsg + " 保存ワークフローを起動しました — 数分後にPDFがGitHubへ保存されます (対象: TDnet掲載期間内の決算短信。以後の新しい短信も自動保存されます)。";
  }
  return listMsg + " ワークフローの手動起動には、トークンに Actions: Read and write 権限が必要です。次回の定期更新 (平日1日5回) で自動的に保存されます。";
}

function disclosureRow(x) {
  const read = x.is_read
    ? '<span class="badge ok">閲覧済</span>'
    : '<span class="badge unread">未閲覧</span>';
  const corr = x.doc_type && x.doc_type.startsWith("訂正") ? "warn" : "market";
  return `<tr>
    <td>${read}</td>
    <td class="code-cell">${h(x.code)}</td>
    <td>${h(x.name)}</td>
    <td class="num">${h(x.market_cap_label || "-")}</td>
    <td><span class="badge ${corr}">${h(x.doc_type || "")}</span></td>
    <td><a class="link" href="#/disclosures/${x.id}">${h(x.title)}</a></td>
    <td>${fmtDateTime(x.published_at)}</td>
    <td>${fmtDateTime(x.fetched_at)}</td>
    <td><a class="btn small" href="#/disclosures/${x.id}">開く</a></td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// 決算短信詳細（PDF ビューア）
// ---------------------------------------------------------------------------
async function disclosureDetail(app, id) {
  const d = await api.get("/disclosures/" + id);
  // 開いたら閲覧済みにする
  if (!d.is_read) { await api.post(`/disclosures/${id}/read`, { is_read: true }); d.is_read = 1; }
  const pdfUrl = api.pdfUrl(id);
  const isExternal = /^https?:/.test(pdfUrl);
  const hasPdf = !!pdfUrl;
  // リポジトリに恒久保存されたPDFがあれば最優先で使う (同一オリジンのため
  // 埋め込み表示が確実に動き、TDnetの掲載期間後も閲覧できる)
  const localPdf = findArchivedPdf(await loadPdfIndex(), d.code, d.published_at);
  const downloadLink = !hasPdf ? ""
    : api.local
      ? `<a class="btn small ghost" href="${pdfUrl}" ${isExternal ? 'target="_blank" rel="noopener"' : `download="${h(d.pdf_path || d.code + ".pdf")}"`}>⬇ ダウンロード</a>`
      : `<a class="btn small ghost" href="${pdfUrl}?download=1">⬇ ダウンロード</a>`;
  // 外部PDF (TDnet) はサイト側の制約で埋め込み表示できない場合があるため、
  // 開くボタンを主動線にし、埋め込みはベストエフォートとする
  const viewer = localPdf
    ? `<div class="pdf-toolbar">
         <span class="badge ok">📌 リポジトリ保存済み</span>
         <a class="btn" href="${localPdf}" target="_blank" rel="noopener">📄 PDFを開く（保存版）</a>
         ${isExternal ? `<a class="btn small ghost" href="${pdfUrl}" target="_blank" rel="noopener">TDnet版を開く</a>` : ""}
       </div>
       <iframe class="pdf-frame" src="${localPdf}" title="PDF"></iframe>
       <div class="meta-line">このPDFはリポジトリに恒久保存されたものです。TDnetの掲載期間後も閲覧できます。</div>`
    : !hasPdf
    ? `<div class="empty">この資料のPDFはTDnetの掲載期間(約1ヶ月)を過ぎたため取得できません。<br>
         <a class="btn" style="margin-top:10px;display:inline-block" target="_blank" rel="noopener"
            href="https://www.google.com/search?q=${encodeURIComponent((d.title || "").slice(0, 60) + " PDF")}">🔎 Webで検索する</a></div>`
    : isExternal
      ? `<div class="pdf-toolbar">
           <a class="btn" href="${pdfUrl}" target="_blank" rel="noopener">📄 PDFを開く（TDnet）</a>
           ${downloadLink}
         </div>
         <iframe class="pdf-frame" src="${pdfUrl}" title="PDF"></iframe>
         <div class="meta-line">PDFはTDnet(適時開示情報閲覧サービス)のものです。上の枠に表示されない場合は「📄 PDFを開く」で新しいタブで開いてください。</div>`
      : `<div class="pdf-toolbar">
           <a class="btn small" href="${pdfUrl}" target="_blank">🔍 外部ブラウザで開く</a>
           ${downloadLink}
         </div>
         <iframe class="pdf-frame" src="${pdfUrl}" title="PDF"></iframe>`;
  app.innerHTML = `
    <a class="back-link" href="#/disclosures">← 決算短信一覧へ戻る</a>
    <div class="page-head"><h1>${h(d.title)}</h1></div>
    <div class="grid cols-2" style="grid-template-columns:2fr 1fr">
      <div class="card">
        ${viewer}
      </div>
      <div>
        <div class="card">
          <h2>資料情報</h2>
          <dl class="kv">
            <dt>銘柄</dt><dd><a class="link" href="#/stock/${h(d.code)}">${h(d.code)} ${h(d.name)}</a></dd>
            <dt>種別</dt><dd>${h(d.doc_type || "")}</dd>
            <dt>公開日時</dt><dd>${fmtDateTime(d.published_at)}</dd>
            <dt>取得日時</dt><dd>${fmtDateTime(d.fetched_at)}</dd>
            <dt>閲覧状態</dt><dd>${d.is_read ? '<span class="badge ok">閲覧済</span>' : '<span class="badge unread">未閲覧</span>'}</dd>
          </dl>
          <div style="margin-top:10px"><button class="btn ghost small" id="toggleRead">${d.is_read ? "未閲覧に戻す" : "閲覧済にする"}</button></div>
        </div>
        <div class="card" style="margin-top:16px">
          <h2>コメント / メモ</h2>
          <textarea id="comment" placeholder="この決算のメモを入力">${h(d.comment || "")}</textarea>
          <div style="margin-top:8px;text-align:right"><button class="btn small" id="saveComment">保存</button></div>
        </div>
      </div>
    </div>`;
  el("saveComment").onclick = async () => {
    await api.patch("/disclosures/" + id, { comment: el("comment").value });
    toast("コメントを保存しました");
  };
  el("toggleRead").onclick = async () => {
    const r = await api.patch("/disclosures/" + id, { is_read: !d.is_read });
    d.is_read = r.is_read;
    disclosureDetail(app, id);
  };
}

// ---------------------------------------------------------------------------
// 銘柄詳細画面
// ---------------------------------------------------------------------------
route("stock", async (app, rest) => {
  const code = rest[0];
  const d = await api.get("/stocks/" + code);
  const nextSched = (d.schedules || []).find((s) => s.announce_date >= new Date().toISOString().slice(0, 10));
  app.innerHTML = `
    <a class="back-link" href="#/schedule">← 決算予定一覧へ戻る</a>
    <div class="detail-head">
      <span class="code">${h(d.code)}</span><span class="name">${h(d.name)}</span>
      <span class="badge market">${h(d.market || "")}</span>${regBadge(d.is_registered)}
    </div>
    <div class="grid cols-2" style="margin-top:14px">
      <div class="card">
        <h2>基本情報</h2>
        <dl class="kv">
          <dt>市場</dt><dd>${h(d.market || "")}</dd>
          <dt>業種</dt><dd>${h(d.sector || "")}</dd>
          <dt>時価総額</dt><dd>${h(d.market_cap_label)}</dd>
          <dt>次回決算予定</dt><dd>${nextSched ? fmtDate(nextSched.announce_date) + " " + h(nextSched.fiscal_type || "") : "-"}</dd>
          <dt>登録状況</dt><dd>${d.is_registered ? h(d.registration.holding_type) + " / 重要度 " + stars(d.registration.importance) : "未登録"}</dd>
        </dl>
        <div style="margin-top:12px">
          ${d.is_registered
            ? `<button class="btn ghost small" data-edit="${h(d.code)}">登録内容を編集</button>
               <button class="btn danger small" data-del="${h(d.code)}">登録解除</button>`
            : `<button class="btn" data-reg="${h(d.code)}" data-name="${h(d.name)}">＋ マイ銘柄に登録</button>`}
        </div>
      </div>
      <div class="card">
        <h2>決算予定</h2>
        ${(d.schedules && d.schedules.length) ? `<div class="table-wrap"><table><thead><tr><th>予定日</th><th>種別</th><th>時刻</th></tr></thead>
          <tbody>${d.schedules.map((s) => `<tr><td>${fmtDate(s.announce_date)}</td><td>${h(s.fiscal_type || "")}</td><td>${h(s.announce_time || "")}</td></tr>`).join("")}</tbody></table></div>`
          : '<div class="empty">決算予定はありません</div>'}
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h2>過去の決算短信 <span class="count">${(d.disclosures || []).length}件</span></h2>
      ${(d.disclosures && d.disclosures.length) ? `<div class="table-wrap"><table>
        <thead><tr><th>状態</th><th>種別</th><th>タイトル</th><th>公開日時</th><th></th></tr></thead>
        <tbody>${d.disclosures.map((x) => `<tr>
          <td>${x.is_read ? '<span class="badge ok">閲覧済</span>' : '<span class="badge unread">未閲覧</span>'}</td>
          <td><span class="badge market">${h(x.doc_type || "")}</span></td>
          <td><a class="link" href="#/disclosures/${x.id}">${h(x.title)}</a></td>
          <td>${fmtDateTime(x.published_at)}</td>
          <td><a class="btn small" href="#/disclosures/${x.id}">開く</a></td></tr>`).join("")}</tbody></table></div>`
        : '<div class="empty">取得済みの決算短信はありません</div>'}
    </div>`;
  app.querySelectorAll("button[data-reg]").forEach((b) => b.onclick = () => openRegisterModal(b.dataset.reg, b.dataset.name));
  app.querySelectorAll("button[data-edit]").forEach((b) => b.onclick = () => openEditModal(b.dataset.edit));
  app.querySelectorAll("button[data-del]").forEach((b) => b.onclick = () => removeMyStock(b.dataset.del));
});

// ---------------------------------------------------------------------------
// 登録 / 編集モーダル
// ---------------------------------------------------------------------------
function modal(html) {
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = `<div class="modal">${html}</div>`;
  wrap.addEventListener("click", (e) => { if (e.target === wrap) wrap.remove(); });
  document.body.appendChild(wrap);
  return wrap;
}

function holdingOptions(sel) {
  return HOLDING_TYPES.map((t) => `<option ${t === sel ? "selected" : ""}>${t}</option>`).join("");
}

function openRegisterModal(code, name) {
  const m = modal(`
    <h3>マイ銘柄に登録</h3>
    <div class="field"><label>銘柄</label><input value="${h(code)} ${h(name || "")}" disabled></div>
    <div class="field"><label>保有区分</label><select id="m_holding">${holdingOptions("監視中")}</select></div>
    <div class="field"><label>重要度 (1〜5)</label><input id="m_importance" type="number" min="1" max="5" value="3"></div>
    <div class="field"><label>メモ</label><textarea id="m_memo"></textarea></div>
    <div class="field"><label><input type="checkbox" id="m_notify" checked style="width:auto"> 決算通知を受け取る</label></div>
    <div class="modal-actions"><button class="btn ghost" id="m_cancel">キャンセル</button><button class="btn" id="m_save">登録</button></div>`);
  m.querySelector("#m_cancel").onclick = () => m.remove();
  m.querySelector("#m_save").onclick = async () => {
    await api.post("/mystocks", {
      code,
      holding_type: m.querySelector("#m_holding").value,
      importance: Number(m.querySelector("#m_importance").value),
      memo: m.querySelector("#m_memo").value,
      notify: m.querySelector("#m_notify").checked ? 1 : 0,
    });
    m.remove();
    toast("マイ銘柄に登録しました");
    render();
  };
}

async function openEditModal(code) {
  const d = await api.get("/stocks/" + code);
  const r = d.registration || {};
  const m = modal(`
    <h3>${h(code)} ${h(d.name)} の編集</h3>
    <div class="field"><label>保有区分</label><select id="m_holding">${holdingOptions(r.holding_type)}</select></div>
    <div class="field"><label>重要度 (1〜5)</label><input id="m_importance" type="number" min="1" max="5" value="${r.importance || 3}"></div>
    <div class="field"><label>メモ</label><textarea id="m_memo">${h(r.memo || "")}</textarea></div>
    <div class="field"><label><input type="checkbox" id="m_notify" ${r.notify ? "checked" : ""} style="width:auto"> 決算通知を受け取る</label></div>
    <div class="modal-actions"><button class="btn ghost" id="m_cancel">キャンセル</button><button class="btn" id="m_save">保存</button></div>`);
  m.querySelector("#m_cancel").onclick = () => m.remove();
  m.querySelector("#m_save").onclick = async () => {
    await api.patch("/mystocks/" + code, {
      holding_type: m.querySelector("#m_holding").value,
      importance: Number(m.querySelector("#m_importance").value),
      memo: m.querySelector("#m_memo").value,
      notify: m.querySelector("#m_notify").checked ? 1 : 0,
    });
    m.remove();
    toast("更新しました");
    render();
  };
}

// ---------------------------------------------------------------------------
// 決算短信の自動取得
// ---------------------------------------------------------------------------
async function runFetch() {
  const btns = document.querySelectorAll("#globalFetch, #fetchMy");
  btns.forEach((b) => (b.disabled = true));
  try {
    const r = await api.post("/fetch");
    toast(r.message || `${r.fetched}件取得しました`);
    render();
  } catch (e) {
    toast("取得に失敗しました: " + e.message, true);
  } finally {
    btns.forEach((b) => (b.disabled = false));
  }
}

// ---------------------------------------------------------------------------
// 銘柄分析 / 銘柄比較 (クライアントサイド機能層)
//
// これらのタブはバックエンドに依存せず、以下のデータで動作する:
// - data/financials.json : GitHub Actions が蓄積する財務数値の推移 (遅延ロード)
// - TDnet ミラーAPI (やのしん) : 銘柄単位の過去2年分の決算短信メタデータ
// - localStorage : 分析コメント・取得済み開示履歴・比較銘柄リスト
// ---------------------------------------------------------------------------
let _finCache = null;
async function loadFinancials() {
  if (_finCache) return _finCache;
  try {
    const res = await fetch("data/financials.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _finCache = (data && data.stocks) ? data : { stocks: {} };
  } catch (e) {
    _finCache = { stocks: {} };
  }
  return _finCache;
}

let _priceCache = null;
async function loadPrices() {
  if (_priceCache) return _priceCache;
  try {
    const res = await fetch("data/prices.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _priceCache = (data && data.stocks) ? data : { stocks: {} };
  } catch (e) {
    _priceCache = { stocks: {} };
  }
  return _priceCache;
}

let _reactCache = null;
async function loadReactions() {
  if (_reactCache) return _reactCache;
  try {
    const res = await fetch("data/reactions.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _reactCache = (data && Array.isArray(data.events)) ? data : { events: [], tail: { dates: [], closes: {} } };
  } catch (e) {
    _reactCache = { events: [], tail: { dates: [], closes: {} } };
  }
  return _reactCache;
}

let _alertCache = null;
async function loadAlerts() {
  if (_alertCache) return _alertCache;
  try {
    const res = await fetch("data/alerts.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _alertCache = (data && Array.isArray(data.alerts)) ? data : { alerts: [] };
  } catch (e) {
    _alertCache = { alerts: [] };
  }
  return _alertCache;
}

const ANALYSIS_KEY = "kessan_analysis_v1";
function loadAnalysisState() {
  try {
    const s = JSON.parse(localStorage.getItem(ANALYSIS_KEY));
    if (s && typeof s === "object") {
      return { comments: s.comments || {}, history: s.history || {} };
    }
  } catch (e) { /* 初期化 */ }
  return { comments: {}, history: {} };
}
const analysisState = loadAnalysisState();
function saveAnalysisState() {
  try { localStorage.setItem(ANALYSIS_KEY, JSON.stringify(analysisState)); } catch (e) { /* 容量超過等は無視 */ }
}

// ---- バックアップ/復元 ----
// マイ銘柄・閲覧状態・分析コメント等はブラウザ(localStorage)にのみ保存される
// ため、キャッシュクリアや端末変更で消失するリスクがある。長期運用(5年)の
// 前提でJSONファイルへのエクスポート/インポートを提供する。
const BACKUP_KEYS = [
  "kessan_local_v1",     // マイ銘柄・サンプルモードの取得済み短信
  "kessan_overlay_v1",   // 実データ短信の閲覧済み/コメント
  "kessan_seen_v1",      // 新着判定の既知キー
  "kessan_analysis_v1",  // 分析コメント・取得済み履歴
  "kessan_compare_v1",   // 比較銘柄リスト
  "kessan_settings_v1",  // アラート設定・自動バックアップ設定 (Actionsも参照)
];

function buildBackupPayload() {
  const payload = { app: "kessan-navi", version: 1, data: {} };
  for (const k of BACKUP_KEYS) {
    try {
      const v = localStorage.getItem(k);
      if (v != null) payload.data[k] = v;
    } catch (e) { /* 読めないキーはスキップ */ }
  }
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  payload.exported_at = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  return payload;
}

function downloadBackup() {
  const payload = buildBackupPayload();
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  const blob = new Blob([JSON.stringify(payload, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `kessan-backup-${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
  toast("バックアップファイルをダウンロードしました");
}

function applyBackupPayload(payload) {
  if (!payload || payload.app !== "kessan-navi" || !payload.data) {
    toast("決算ナビのバックアップデータではありません", true);
    return;
  }
  const keys = Object.keys(payload.data).filter((k) => BACKUP_KEYS.includes(k));
  if (!keys.length) {
    toast("復元できるデータがありません", true);
    return;
  }
  if (!confirm(`バックアップ(${payload.exported_at || "日時不明"})から${keys.length}項目を復元します。現在のブラウザ内データは上書きされます。よろしいですか?`)) return;
  for (const k of keys) {
    try { localStorage.setItem(k, payload.data[k]); } catch (e) { /* 容量超過等 */ }
  }
  toast("復元しました。再読み込みします…");
  setTimeout(() => location.reload(), 800);
}

function restoreBackup(file) {
  const reader = new FileReader();
  reader.onload = () => {
    let payload;
    try {
      payload = JSON.parse(String(reader.result));
    } catch (e) {
      toast("バックアップファイルを読み込めません (JSONが不正)", true);
      return;
    }
    applyBackupPayload(payload);
  };
  reader.readAsText(file);
}

// ---------------------------------------------------------------------------
// 設定 (アラート条件・gitへの自動バックアップ)
// ---------------------------------------------------------------------------
const SETTINGS_KEY = "kessan_settings_v1";
const GH_TOKEN_KEY = "kessan_gh_token_v1";      // トークンはバックアップに含めない
const BACKUP_META_KEY = "kessan_backup_meta_v1"; // 最終push状態 (同じく含めない)
const AUTO_BACKUP_MIN_MS = 10 * 60 * 1000;       // コミットのスパム防止 (最短10分)

// scripts/generate_alerts.py の DEFAULT_SETTINGS と同じ既定値
const ALERT_DEFAULT_LEVELS = {
  "5": { price_move: 1, pct: 3, wk52: 1, volume: 1, vol_x: 2, earnings: 1, disclosure: 1, streak: 1, reaction: 1, rpct: 5 },
  "4": { price_move: 1, pct: 3, wk52: 1, volume: 1, vol_x: 2, earnings: 1, disclosure: 1, streak: 1, reaction: 1, rpct: 5 },
  "3": { price_move: 1, pct: 5, wk52: 0, volume: 0, vol_x: 2, earnings: 1, disclosure: 1, streak: 0, reaction: 1, rpct: 5 },
  "2": { price_move: 0, pct: 5, wk52: 0, volume: 0, vol_x: 2, earnings: 1, disclosure: 1, streak: 0, reaction: 0, rpct: 5 },
  "1": { price_move: 0, pct: 5, wk52: 0, volume: 0, vol_x: 2, earnings: 0, disclosure: 0, streak: 0, reaction: 0, rpct: 5 },
};
const ALERT_ICONS = {
  price_move: "📈", wk52_high: "🚀", wk52_low: "🔻", volume: "📊", earnings: "📅",
  streak_down: "📉", streak_up: "📈", reaction: "🎯",
};
function alertIcon(t) {
  if (t && t.indexOf("disclosure") === 0) return "📢";
  return ALERT_ICONS[t] || "🔔";
}

function loadAppSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY));
    if (s && typeof s === "object") return s;
  } catch (e) { /* 初期化 */ }
  return {};
}
const appSettings = loadAppSettings();
function saveAppSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(appSettings)); } catch (e) { /* 無視 */ }
}

function alertConf() {
  const a = appSettings.alerts || {};
  const levels = {};
  for (const k of ["5", "4", "3", "2", "1"]) {
    levels[k] = Object.assign({}, ALERT_DEFAULT_LEVELS[k], (a.levels || {})[k] || {});
  }
  return { email: a.email !== false, levels };
}

function ghToken() {
  try { return localStorage.getItem(GH_TOKEN_KEY) || ""; } catch (e) { return ""; }
}

function repoInfo() {
  // GitHub Pages ({owner}.github.io/{repo}/) から導出。ローカル実行時は既定値。
  const m = /^([^.]+)\.github\.io$/.exec(location.hostname);
  const seg = location.pathname.split("/").filter(Boolean);
  if (m && seg.length) return { owner: m[1], repo: seg[0] };
  return { owner: "kojit1229", repo: "stock_analyze" };
}

function strHash(s) {
  let x = 5381;
  for (let i = 0; i < s.length; i++) x = ((x * 33) ^ s.charCodeAt(i)) >>> 0;
  return x.toString(36) + ":" + s.length;
}

function loadBackupMeta() {
  try {
    const m = JSON.parse(localStorage.getItem(BACKUP_META_KEY));
    if (m && typeof m === "object") return m;
  } catch (e) { /* 初期化 */ }
  return {};
}
function saveBackupMeta(meta) {
  try { localStorage.setItem(BACKUP_META_KEY, JSON.stringify(meta)); } catch (e) { /* 無視 */ }
}

// GitHub contents API で config/user_data.json へコミットする。
// 成功: "pushed" / 変更なし: "unchanged" / 未設定: "no-token"
async function pushBackupToGit(manual) {
  const token = ghToken();
  if (!token) return "no-token";
  const body = JSON.stringify(buildBackupPayload(), null, 1);
  const hash = strHash(body);
  const meta = loadBackupMeta();
  if (!manual && meta.hash === hash) return "unchanged";
  const { owner, repo } = repoInfo();
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/config/user_data.json`;
  const headers = { Authorization: "Bearer " + token, Accept: "application/vnd.github+json" };
  let sha;
  const g = await fetch(url + "?ref=main", { headers });
  if (g.ok) sha = (await g.json()).sha;
  else if (g.status !== 404) throw new Error("GitHub API " + g.status + " (トークンの権限を確認してください)");
  const put = await fetch(url, {
    method: "PUT",
    headers,
    body: JSON.stringify({
      message: "backup: ユーザーデータの自動バックアップ",
      content: btoa(unescape(encodeURIComponent(body))),
      branch: "main",
      sha,
    }),
  });
  if (!put.ok) {
    const err = await put.json().catch(() => ({}));
    throw new Error("バックアップのpushに失敗: " + (err.message || put.status));
  }
  saveBackupMeta({ hash, at: buildBackupPayload().exported_at, atMs: Date.now() });
  return "pushed";
}

let _abTimer = null;
function scheduleAutoBackup() {
  if (!(appSettings.autoBackup && appSettings.autoBackup.enabled)) return;
  if (!ghToken()) return;
  if (_abTimer) clearTimeout(_abTimer);
  _abTimer = setTimeout(() => {
    _abTimer = null;
    const meta = loadBackupMeta();
    if (meta.atMs && Date.now() - meta.atMs < AUTO_BACKUP_MIN_MS) return;
    pushBackupToGit(false).then((r) => {
      if (r === "pushed") toast("gitへ自動バックアップしました");
    }).catch(() => { /* 自動実行なので静かに失敗 (設定画面から手動で確認可能) */ });
  }, 5000);
}

async function restoreFromGit() {
  let payload = null;
  try {
    const res = await fetch("../config/user_data.json", { cache: "no-cache" });
    if (res.ok) payload = await res.json();
  } catch (e) { /* フォールバックへ */ }
  if (!payload) {
    try {
      const { owner, repo } = repoInfo();
      const res = await fetch(
        `https://api.github.com/repos/${owner}/${repo}/contents/config/user_data.json?ref=main`,
        { headers: { Accept: "application/vnd.github.raw+json" } });
      if (!res.ok) throw new Error("HTTP " + res.status);
      payload = await res.json();
    } catch (e) {
      toast("gitのバックアップを取得できません (まだ一度もバックアップされていない可能性): " + e.message, true);
      return;
    }
  }
  applyBackupPayload(payload);
}

route("settings", async (app) => {
  const conf = alertConf();
  const ab = appSettings.autoBackup || {};
  const meta = loadBackupMeta();
  const { owner, repo } = repoInfo();
  const levelRow = (k) => {
    const c = conf.levels[k];
    return `<tr data-level="${k}">
      <td style="white-space:nowrap"><span class="star">${stars(Number(k))}</span></td>
      <td style="white-space:nowrap"><span style="display:inline-flex;align-items:center;gap:4px">
        <input type="checkbox" data-k="price_move" ${c.price_move ? "checked" : ""}> ±
        <input type="number" data-k="pct" value="${h(String(c.pct))}" min="0.5" step="0.5" style="width:64px"> %以上</span></td>
      <td style="text-align:center"><input type="checkbox" data-k="wk52" ${c.wk52 ? "checked" : ""}></td>
      <td style="white-space:nowrap"><span style="display:inline-flex;align-items:center;gap:4px">
        <input type="checkbox" data-k="volume" ${c.volume ? "checked" : ""}> 平均の
        <input type="number" data-k="vol_x" value="${h(String(c.vol_x))}" min="1" step="0.5" style="width:56px"> 倍以上</span></td>
      <td style="text-align:center"><input type="checkbox" data-k="earnings" ${c.earnings ? "checked" : ""}></td>
      <td style="text-align:center"><input type="checkbox" data-k="disclosure" ${c.disclosure ? "checked" : ""}></td>
      <td style="text-align:center"><input type="checkbox" data-k="streak" ${c.streak ? "checked" : ""}></td>
      <td style="white-space:nowrap"><span style="display:inline-flex;align-items:center;gap:4px">
        <input type="checkbox" data-k="reaction" ${c.reaction ? "checked" : ""}> ±
        <input type="number" data-k="rpct" value="${h(String(c.rpct))}" min="1" step="0.5" style="width:56px"> %以上</span></td>
    </tr>`;
  };
  app.innerHTML = `
    <div class="page-head"><h1>設定</h1><span class="sub">アラート条件とバックアップを設定します</span></div>

    <div class="card" style="margin-bottom:14px">
      <h2>🔔 終値アラート <span class="count">重要度ごとに設定</span></h2>
      <div class="meta-line" style="margin-bottom:10px">
        平日の引け後 (15:35以降のデータ更新時)、マイ銘柄の終値をチェックしてアラートを生成します。
        アラートはホーム画面に表示され、メール通知ONの場合はGitHubのIssueが作成されて通知メールが届きます。<br>
        <b>※ アラートを使うには、下の「gitへの自動バックアップ」を有効にしてください</b>
        (GitHub Actionsがマイ銘柄と設定を読むため)。
      </div>
      <div class="table-wrap"><table id="alertTable">
        <thead><tr><th>重要度</th><th>株価変動</th><th>52週高値/安値</th><th>出来高急増</th><th>決算前日/当日</th><th title="業績予想修正・配当予想修正・自己株式取得・訂正決算短信の開示">重要開示</th><th title="終値ベースで3日以上の連続下落/連続上昇">3日連続±</th><th title="決算発表の当日/翌営業日に閾値以上動いたとき">決算反応</th></tr></thead>
        <tbody>${["5", "4", "3", "2", "1"].map(levelRow).join("")}</tbody>
      </table></div>
      <div style="margin-top:10px">
        <label style="display:inline-flex;align-items:center;gap:6px"><input type="checkbox" id="alert_email" ${conf.email ? "checked" : ""}>
          メール通知 (新規アラート発生時にGitHub Issueを作成し、@${h(owner)} へメンション → GitHubから通知メールが届きます)</label>
      </div>
      <div class="meta-line" style="margin-top:6px">メールが届かない場合は GitHub の Settings → Notifications で「Participating, @mentions」のEmail通知がONになっているか確認してください。</div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <h2>💾 gitへの自動バックアップ</h2>
      <div class="meta-line" style="margin-bottom:10px">
        マイ銘柄・閲覧状態・分析コメント・この設定を、リポジトリ <b>${h(owner)}/${h(repo)}</b> の
        <code>config/user_data.json</code> へ自動コミットします (変更があったとき、最短10分間隔)。
        端末変更やキャッシュクリア後も「gitから復元」で戻せます。
      </div>
      <div class="field" style="max-width:560px">
        <label>GitHub トークン (Fine-grained PAT / このリポジトリの Contents: Read and write 権限のみ)</label>
        <div style="display:flex;gap:6px">
          <input type="password" id="gh_token" value="${h(ghToken())}" placeholder="github_pat_..." style="flex:1">
          <button class="btn small" id="gh_token_save">保存</button>
        </div>
      </div>
      <div class="meta-line" style="margin:6px 0 10px">
        トークンは <a class="link" href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">GitHub設定 → Fine-grained tokens</a> で作成:
        Repository access = Only select repositories (${h(repo)}) / Permissions = Contents: Read and write。
        トークンはこのブラウザにのみ保存され、バックアップファイルには含まれません。
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <label style="display:inline-flex;align-items:center;gap:6px;white-space:nowrap"><input type="checkbox" id="ab_enabled" ${ab.enabled ? "checked" : ""}> 自動バックアップを有効にする</label>
        <button class="btn small" id="backupNow">今すぐバックアップ</button>
        <button class="btn small ghost" id="restoreGit">gitから復元</button>
        <span class="meta-line" id="ab_status" style="margin:0">${meta.at ? `最終バックアップ: ${h(meta.at)}` : "まだバックアップされていません"}</span>
      </div>
    </div>

    <div class="card">
      <h2>📥 ファイルへのバックアップ</h2>
      <div class="meta-line">gitを使わない手動バックアップは<a class="link" href="#/mystocks">マイ銘柄</a>画面の「⬇ バックアップ / ⬆ 復元」から行えます。</div>
    </div>`;

  const collectAlerts = () => {
    const levels = {};
    app.querySelectorAll("#alertTable tbody tr").forEach((tr) => {
      const k = tr.dataset.level;
      const get = (name) => tr.querySelector(`[data-k="${name}"]`);
      levels[k] = {
        price_move: get("price_move").checked ? 1 : 0,
        pct: Number(get("pct").value) || ALERT_DEFAULT_LEVELS[k].pct,
        wk52: get("wk52").checked ? 1 : 0,
        volume: get("volume").checked ? 1 : 0,
        vol_x: Number(get("vol_x").value) || 2,
        earnings: get("earnings").checked ? 1 : 0,
        disclosure: get("disclosure").checked ? 1 : 0,
        streak: get("streak").checked ? 1 : 0,
        reaction: get("reaction").checked ? 1 : 0,
        rpct: Number(get("rpct").value) || 5,
      };
    });
    appSettings.alerts = { email: el("alert_email").checked, levels };
    saveAppSettings();
    toast("アラート設定を保存しました");
  };
  el("alertTable").addEventListener("change", collectAlerts);
  el("alert_email").addEventListener("change", collectAlerts);

  el("gh_token_save").onclick = () => {
    try { localStorage.setItem(GH_TOKEN_KEY, el("gh_token").value.trim()); } catch (e) { /* 無視 */ }
    toast("トークンを保存しました");
  };
  el("ab_enabled").onchange = () => {
    appSettings.autoBackup = { enabled: el("ab_enabled").checked };
    saveAppSettings();
    if (el("ab_enabled").checked && !ghToken()) toast("トークンを登録すると自動バックアップが動き始めます", true);
    else toast("自動バックアップ設定を保存しました");
  };
  el("backupNow").onclick = async () => {
    el("backupNow").disabled = true;
    try {
      const r = await pushBackupToGit(true);
      if (r === "no-token") toast("先にGitHubトークンを保存してください", true);
      else {
        toast("gitへバックアップしました");
        el("ab_status").textContent = "最終バックアップ: " + (loadBackupMeta().at || "");
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      el("backupNow").disabled = false;
    }
  };
  el("restoreGit").onclick = restoreFromGit;
});

const COMPARE_KEY = "kessan_compare_v1";
function loadCompareCodes() {
  try {
    const s = JSON.parse(localStorage.getItem(COMPARE_KEY));
    if (Array.isArray(s)) return s.slice(0, 4);
  } catch (e) { /* 初期化 */ }
  return [];
}
let compareCodes = loadCompareCodes();
function saveCompareCodes() {
  try { localStorage.setItem(COMPARE_KEY, JSON.stringify(compareCodes)); } catch (e) { /* 無視 */ }
}

// ---- 数値フォーマット ----
function fmtMoney(v) {
  if (v == null || !isFinite(v)) return "-";
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + "兆円";
  if (a >= 1e8) return Math.round(v / 1e8).toLocaleString("en-US") + "億円";
  return Math.round(v).toLocaleString("en-US") + "円";
}
function fmtPct(v, digits) {
  if (v == null || !isFinite(v)) return "-";
  return v.toFixed(digits == null ? 1 : digits) + "%";
}
function yoyPct(cur, prev) {
  if (cur == null || prev == null || !isFinite(cur) || !isFinite(prev) || prev === 0) return null;
  return ((cur - prev) / Math.abs(prev)) * 100;
}
function periodLabel(d) {
  if (!d || d.length < 7) return String(d || "");
  return d.slice(2, 4) + "/" + String(Number(d.slice(5, 7)));
}
function cagrPct(first, last, years) {
  if (first == null || last == null || first <= 0 || last <= 0 || years <= 0) return null;
  return (Math.pow(last / first, 1 / years) - 1) * 100;
}

// 年次業績から決算シグナル (増収増益・利益率改善・最高益更新など) を判定する
function buildSignals(a) {
  const out = [];
  const n = a.length;
  if (n < 2) return out;
  const L = a[n - 1], P = a[n - 2];
  const push = (label, good) => out.push({ label, good });
  if (L[1] != null && P[1] != null) push(L[1] > P[1] ? "増収" : "減収", L[1] > P[1]);
  if (L[2] != null && P[2] != null) push(L[2] > P[2] ? "営業増益" : "営業減益", L[2] > P[2]);
  const mL = L[1] && L[2] != null ? L[2] / L[1] : null;
  const mP = P[1] && P[2] != null ? P[2] / P[1] : null;
  if (mL != null && mP != null) push(mL > mP ? "営業利益率改善" : "営業利益率悪化", mL > mP);
  if (L[3] != null && P[3] != null) {
    if (P[3] < 0 && L[3] >= 0) push("黒字転換", true);
    else if (P[3] >= 0 && L[3] < 0) push("赤字転落", false);
  }
  if (n >= 3) {
    const nis = a.map((r) => r[3]).filter((v) => v != null);
    if (L[3] != null && nis.length >= 3 && L[3] >= Math.max(...nis)) push("最高益更新", true);
    const revs = a.map((r) => r[1]).filter((v) => v != null);
    if (L[1] != null && revs.length >= 3 && L[1] >= Math.max(...revs)) push("最高売上更新", true);
  }
  return out;
}

// ---- SVGチャート (依存ライブラリなし) ----
const CHART_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a78bfa", "#2dd4bf"];

function fmtAxisVal(v, unit) {
  if (unit === "pct") return v.toFixed(0) + "%";
  if (unit === "yen") return Math.round(v).toLocaleString("en-US");
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(1) + "兆";
  if (a >= 1e8) return Math.round(v / 1e8).toLocaleString("en-US") + "億";
  if (a === 0) return "0";
  return v.toLocaleString("en-US");
}

function chartSVG({ title, labels, series, unit }) {
  const W = 560, H = 220, PL = 64, PR = 10, PT = 12, PB = 30;
  const iw = W - PL - PR, ih = H - PT - PB;
  const all = series.flatMap((s) => s.values).filter((v) => v != null && isFinite(v));
  if (!all.length || !labels.length) {
    return `<div class="chart-box"><div class="chart-title">${h(title)}</div><div class="empty" style="padding:24px 10px">データがありません</div></div>`;
  }
  let min = Math.min(0, ...all), max = Math.max(0, ...all);
  if (min === max) max = min + 1;
  const span = max - min;
  max += span * 0.08;
  if (min < 0) min -= span * 0.05;
  const y = (v) => PT + ih - ((v - min) / (max - min)) * ih;
  const xc = (i) => PL + (i + 0.5) * (iw / labels.length);
  let g = "";
  for (let t = 0; t <= 4; t++) {
    const v = min + ((max - min) * t) / 4, yy = y(v);
    g += `<line x1="${PL}" y1="${yy.toFixed(1)}" x2="${W - PR}" y2="${yy.toFixed(1)}" stroke="#2a3448" stroke-width="1"/>`;
    g += `<text x="${PL - 6}" y="${(yy + 3.5).toFixed(1)}" text-anchor="end" class="chart-tick">${fmtAxisVal(v, unit)}</text>`;
  }
  if (min < 0) {
    g += `<line x1="${PL}" y1="${y(0).toFixed(1)}" x2="${W - PR}" y2="${y(0).toFixed(1)}" stroke="#5b6b85" stroke-width="1.2"/>`;
  }
  const step = labels.length > 8 ? Math.ceil(labels.length / 8) : 1;
  labels.forEach((lb, i) => {
    if (i % step) return;
    g += `<text x="${xc(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="chart-tick">${h(lb)}</text>`;
  });
  const barSeries = series.filter((s) => s.type !== "line");
  if (barSeries.length) {
    const bw = Math.max(4, Math.min(26, ((iw / labels.length) * 0.72) / barSeries.length));
    barSeries.forEach((s, si) => {
      s.values.forEach((v, i) => {
        if (v == null || !isFinite(v)) return;
        const x = xc(i) - (bw * barSeries.length) / 2 + si * bw;
        const top = Math.min(y(v), y(0));
        const hgt = Math.max(1.5, Math.abs(y(0) - y(v)));
        g += `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${(bw - 1.5).toFixed(1)}" height="${hgt.toFixed(1)}" fill="${s.color}" rx="2"/>`;
      });
    });
  }
  series.filter((s) => s.type === "line").forEach((s) => {
    const pts = [];
    s.values.forEach((v, i) => {
      if (v != null && isFinite(v)) pts.push(`${xc(i).toFixed(1)},${y(v).toFixed(1)}`);
    });
    if (pts.length > 1) g += `<polyline points="${pts.join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
    s.values.forEach((v, i) => {
      if (v != null && isFinite(v)) g += `<circle cx="${xc(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="${s.color}"/>`;
    });
  });
  const legend = series.map((s) => `<span class="chart-leg"><i style="background:${s.color}"></i>${h(s.name)}</span>`).join("");
  return `<div class="chart-box"><div class="chart-title">${h(title)}</div>
    <svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img">${g}</svg>
    <div class="chart-legend">${legend}</div></div>`;
}

// ---- 開示履歴アーカイブ (GitHub Actions が構築する静的シャードJSON) ----
// data/history/{コード先頭1文字}.json に過去2年分の決算関連開示が入っている。
// (以前はTDnetミラーAPIへのブラウザ直接アクセスだったが、CORS非対応かつ
//  応答が遅く公開プロキシでも成立しないため、サーバ側で蓄積する方式に変更)
const HIST_SHARD_CACHE = {};

async function loadHistoryShard(prefix) {
  if (HIST_SHARD_CACHE[prefix]) return HIST_SHARD_CACHE[prefix];
  let shard = { codes: {} };
  try {
    const res = await fetch("data/history/" + encodeURIComponent(prefix) + ".json", { cache: "no-cache" });
    if (res.ok) {
      const data = await res.json();
      if (data && data.codes) shard = data;
    }
  } catch (e) { /* アーカイブ未構築 */ }
  HIST_SHARD_CACHE[prefix] = shard;
  return shard;
}

// PDF恒久保存アーカイブ (config/pdf_watchlist.json の銘柄の決算短信PDFを
// GitHub Actions がリポジトリの pdfs/ に保存している) のインデックス
let _pdfIndexCache = null;
async function loadPdfIndex() {
  if (_pdfIndexCache) return _pdfIndexCache;
  let idx = { codes: {} };
  try {
    const res = await fetch("pdfs/index.json", { cache: "no-cache" });
    if (res.ok) {
      const d = await res.json();
      if (d && d.codes) idx = d;
    }
  } catch (e) { /* 未構築 */ }
  _pdfIndexCache = idx;
  return idx;
}

function findArchivedPdf(idx, code, publishedAt) {
  const rows = (idx.codes || {})[code] || [];
  const key = (publishedAt || "").slice(0, 16);
  const r = rows.find((x) => (x[0] || "").slice(0, 16) === key);
  return r ? "pdfs/" + r[3] : null;
}

let _metaInfoCache = null;
async function loadMetaInfo() {
  if (_metaInfoCache) return _metaInfoCache;
  let m = {};
  try {
    const res = await fetch("data/meta.json", { cache: "no-cache" });
    if (res.ok) {
      const data = await res.json();
      if (data && typeof data === "object") m = data;
    }
  } catch (e) { /* 未構築 */ }
  _metaInfoCache = m;
  return m;
}

let _histStateCache = null;
async function loadHistoryStateInfo() {
  if (_histStateCache) return _histStateCache;
  let st = {};
  try {
    const res = await fetch("data/history/state.json", { cache: "no-cache" });
    if (res.ok) {
      const data = await res.json();
      if (data && typeof data === "object") st = data;
    }
  } catch (e) { /* 未構築 */ }
  _histStateCache = st;
  return st;
}

// TDnetミラーのリダイレクタ (rd.php?<URL>) を剥がして直接URLにする
// (リダイレクタは応答が不安定で、PDFが開けない原因になる)
function directPdfUrl(url) {
  const m = /rd\.php\?(https?:\/\/.+)$/.exec(url || "");
  return m ? m[1] : (url || "");
}

async function fetchCompanyHistory(code) {
  const shard = await loadHistoryShard(code[0]);
  const rows = (shard.codes || {})[code] || [];
  return rows.map((r) => ({
    key: (r[0] || "") + "|" + String(r[2] || "").slice(0, 12),
    published_at: r[0],
    doc_type: r[1],
    title: r[2],
    pdf_url: directPdfUrl(r[3] || ""),
  }));
}

async function bulkFetchHistory(codes, statusEl) {
  let ok = 0, total = 0;
  const failed = [];
  for (const code of codes) {
    if (statusEl) statusEl.textContent = `取得中… (${code})`;
    try {
      const items = await fetchCompanyHistory(code);
      analysisState.history[code] = {
        fetched_at: new Date().toISOString().slice(0, 19),
        items,
      };
      saveAnalysisState();
      ok++;
      total += items.length;
    } catch (e) {
      failed.push(code);
    }
  }
  const st = await loadHistoryStateInfo();
  const range = st.oldest ? ` (アーカイブ範囲: ${st.oldest}〜現在${st.complete ? "" : "、過去分を構築中"})` : "";
  const message = failed.length
    ? `完了: ${ok}/${codes.length}銘柄・${total}件を取得 (失敗: ${failed.join(", ")})${range}`
    : `完了: ${ok}銘柄・${total}件の決算短信履歴を取得しました${range}`;
  if (statusEl) statusEl.textContent = message;
  return { ok, message };
}

// 開示履歴の表示 (キャッシュ + 全体データをマージ)
function mergedHistory(code, globalItems) {
  const seen = new Set();
  const out = [];
  const cached = (analysisState.history[code] || {}).items || [];
  for (const it of [...cached, ...(globalItems || [])]) {
    const k = (it.published_at || "").slice(0, 16) + "|" + (it.title || "").slice(0, 30);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(it);
  }
  out.sort((a, b) => ((a.published_at || "") > (b.published_at || "") ? -1 : 1));
  return out;
}

function historyRow(it) {
  const fresh = (() => {
    const s = String(it.published_at || "").slice(0, 10);
    const [y, mo, dd] = s.split("-").map(Number);
    if (!y || !mo || !dd) return false;
    return (Date.now() - new Date(y, mo - 1, dd).getTime()) < 40 * 86400e3;
  })();
  const corr = it.doc_type && it.doc_type.startsWith("訂正") ? "warn" : "market";
  const searchQ = encodeURIComponent(it.title.slice(0, 60) + " PDF");
  const link = it._local
    ? `<a class="link" href="${h(it._local)}" target="_blank" rel="noopener">${h(it.title)}</a> <span class="badge ok" title="リポジトリに恒久保存済み">📌保存済</span>`
    : it.pdf_url
      ? `<a class="link" href="${h(it.pdf_url)}" target="_blank" rel="noopener">${h(it.title)}</a>`
      : h(it.title);
  return `<tr>
    <td>${fmtDateTime(it.published_at)}</td>
    <td><span class="badge ${corr}">${h(it.doc_type || "")}</span></td>
    <td>${link}${(fresh || it._local) ? "" : ` <a class="link" href="https://www.google.com/search?q=${searchQ}" target="_blank" rel="noopener" title="TDnetの掲載期間(約1ヶ月)を過ぎたPDFは削除されている場合があります。Webを検索します">🔎</a>`}</td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// 銘柄分析タブ
// ---------------------------------------------------------------------------
const analysisView = { mode: "a" }; // a=年次 / q=四半期

route("analysis", async (app, rest) => {
  const code = rest && rest[0] ? decodeURIComponent(rest[0]) : null;
  const my = await api.get("/mystocks");
  const myOptions = my.items.map((m) =>
    `<option value="${h(m.code)}" ${m.code === code ? "selected" : ""}>${h(m.code)} ${h(m.name)}</option>`).join("");

  app.innerHTML = `
    <div class="page-head"><h1>銘柄分析</h1><span class="sub">決算短信の情報と数値推移から個別銘柄を分析します</span></div>
    <div class="card" style="margin-bottom:14px">
      <div class="picker-bar">
        <div class="field" style="flex-direction:row;align-items:center;gap:8px">
          <label style="font-size:12px">マイ銘柄から</label>
          <select id="an_select"><option value="">選択してください</option>${myOptions}</select>
        </div>
        <div class="field" style="flex-direction:row;align-items:center;gap:8px">
          <label style="font-size:12px">またはコード</label>
          <input id="an_code" placeholder="例: 7203" value="${code && !my.items.some((m) => m.code === code) ? h(code) : ""}" style="width:110px">
        </div>
        <button class="btn" id="an_show">分析する</button>
      </div>
    </div>
    <div class="meta-line" style="margin-bottom:14px">決算短信の「まとめて取得」と「PDFのGitHub保存」は<a class="link" href="#/disclosures">決算短信</a>タブに移動しました。</div>
    <div id="an_body">${code ? '<div class="loading">読み込み中…</div>' : '<div class="empty">銘柄を選択してください</div>'}</div>`;

  const show = () => {
    const c = el("an_select").value || el("an_code").value.trim();
    if (c) location.hash = "#/analysis/" + encodeURIComponent(c);
  };
  el("an_show").onclick = show;
  el("an_select").onchange = show;
  if (code) await renderAnalysisBody(code);
});

async function renderAnalysisBody(code) {
  const body = el("an_body");
  if (!body) return;
  let stock;
  try {
    stock = await api.get("/stocks/" + encodeURIComponent(code));
  } catch (e) {
    body.innerHTML = `<div class="empty">エラー: ${h(e.message)}</div>`;
    return;
  }
  const [findata, metaInfo, pricesData, reactions] = await Promise.all([
    loadFinancials(), loadMetaInfo(), loadPrices(), loadReactions(),
  ]);
  const finGot = Object.keys(findata.stocks || {}).filter((c) => {
    const v = findata.stocks[c];
    return v && ((v.a && v.a.length) || (v.q && v.q.length));
  }).length;
  const finTotal = (metaInfo.counts && metaInfo.counts.stocks) || null;
  const fin = findata.stocks[code] || { a: [], q: [] };
  const rows = analysisView.mode === "q" ? (fin.q || []) : (fin.a || []);
  const labels = rows.map((r) => periodLabel(r[0]));
  const val = (i) => rows.map((r) => (r[i] == null ? null : r[i]));
  const rev = val(1), op = val(2), ni = val(3), eps = val(4);
  const opm = rows.map((r) => (r[1] && r[2] != null ? (r[2] / r[1]) * 100 : null));

  // 直近期のサマリ (年次ベース)
  const a = fin.a || [];
  const last = a[a.length - 1], prev = a[a.length - 2];
  const sum = (label, v, yoy, fmt) => `
    <div class="card stat"><div class="label">${label}</div>
      <div class="value" style="font-size:20px">${fmt(v)}</div>
      ${yoy == null ? "" : `<div class="meta-line" style="margin-top:2px">前期比 <span style="color:${yoy >= 0 ? "#4ade80" : "#f87171"}">${yoy >= 0 ? "+" : ""}${yoy.toFixed(1)}%</span></div>`}
    </div>`;

  // アーカイブ(静的シャード)から履歴を読み込み、直近の開示データとマージ表示する
  let shardItems = [];
  try {
    shardItems = await fetchCompanyHistory(code);
  } catch (e) { /* アーカイブ未構築 */ }
  const histState = await loadHistoryStateInfo();
  const globalItems = (stock.disclosures || []).map((d) => (
    { key: String(d.id), title: d.title, doc_type: d.doc_type, published_at: d.published_at, pdf_url: d.pdf_url || "" }
  ));
  const hist = mergedHistory(code, [...shardItems, ...globalItems]);
  const pdfIdx = await loadPdfIndex();
  for (const it of hist) {
    const lp = findArchivedPdf(pdfIdx, code, it.published_at);
    if (lp) it._local = lp;
  }
  const cachedInfo = analysisState.history[code];
  const comment = analysisState.comments[code] || "";

  body.innerHTML = `
    <div class="detail-head" style="margin-bottom:12px">
      <span class="code">${h(stock.code)}</span><span class="name">${h(stock.name)}</span>
      <span class="badge market">${h(stock.market || "")}</span>
      <span class="badge market">${h(stock.sector || "")}</span>
      <span class="sub">時価総額 ${h(stock.market_cap_label || "-")}</span>
      <a class="link" href="#/stock/${h(stock.code)}" style="margin-left:auto;font-size:12px">銘柄詳細 →</a>
    </div>
    ${last ? `<div class="grid cols-4" style="margin-bottom:14px">
      ${sum(`売上高 (${periodLabel(last[0])}期)`, last[1], yoyPct(last[1], prev && prev[1]), fmtMoney)}
      ${sum("営業利益", last[2], yoyPct(last[2], prev && prev[2]), fmtMoney)}
      ${sum("営業利益率", last[1] && last[2] != null ? (last[2] / last[1]) * 100 : null, null, fmtPct)}
      ${sum("EPS", last[4], yoyPct(last[4], prev && prev[4]), (v) => (v == null ? "-" : v.toFixed(1) + "円"))}
    </div>` : `<div class="card" style="margin-bottom:14px"><div class="empty">この銘柄の財務数値(売上高・利益など)はまだ取得されていません。<br>
      全${finTotal ? finTotal.toLocaleString("en-US") : ""}銘柄をコード順に自動巡回中です(取得済み: ${finGot.toLocaleString("en-US")}銘柄・毎時拡大)。<br>
      取得され次第、ここに指標と推移チャートが表示されます。</div></div>`}
    ${last ? analysisInsightHtml(stock, fin, pricesData.stocks[code]) : ""}
    ${last ? progressHtml(fin) : ""}
    ${financialHealthHtml(stock, fin)}
    ${reactionHtml(code, reactions)}
    <div class="card" style="margin-bottom:14px">
      <h2>📊 決算数値の推移
        <span style="margin-left:auto;display:inline-flex;gap:6px">
          <span class="chip ${analysisView.mode === "a" ? "active" : ""}" id="an_mode_a">年次</span>
          <span class="chip ${analysisView.mode === "q" ? "active" : ""}" id="an_mode_q">四半期</span>
        </span></h2>
      <div class="charts-grid">
        ${chartSVG({ title: "売上高", labels, series: [{ name: "売上高", color: CHART_COLORS[0], values: rev }] })}
        ${chartSVG({ title: "営業利益・純利益", labels, series: [
          { name: "営業利益", color: CHART_COLORS[1], values: op },
          { name: "純利益", color: CHART_COLORS[2], values: ni }] })}
        ${chartSVG({ title: "営業利益率", labels, unit: "pct", series: [{ name: "営業利益率", color: CHART_COLORS[4], values: opm, type: "line" }] })}
        ${chartSVG({ title: "EPS", labels, unit: "yen", series: [{ name: "EPS(円)", color: CHART_COLORS[3], values: eps, type: "line" }] })}
      </div>
      ${performanceTableHtml(rows, analysisView.mode)}
      <div class="meta-line">出典: Yahoo Finance (年次は直近${(fin.a || []).length}期 / 四半期は取得できる範囲)。単位: 円。PER・PSRは時価総額と直近通期実績からの概算。</div>
    </div>
    <div class="grid cols-2" style="grid-template-columns:3fr 2fr">
      <div class="card">
        <h2>📄 決算短信・開示履歴 <span class="count">${hist.length}件</span>
          <span style="margin-left:auto"><button class="btn small ghost" id="an_fetch_one">この銘柄の2年分を取得</button></span></h2>
        ${histState.oldest ? `<div class="meta-line">アーカイブ範囲: ${h(histState.oldest)} 〜 現在${histState.complete ? "" : " (過去2年分へ向けて構築中)"}</div>` : ""}
        ${cachedInfo ? `<div class="meta-line">履歴取得: ${fmtDateTime(cachedInfo.fetched_at)}</div>` : ""}
        <div class="fetch-status" id="an_one_status"></div>
        ${hist.length ? `<div class="table-wrap"><table>
          <thead><tr><th>公開日時</th><th>種別</th><th>タイトル</th></tr></thead>
          <tbody>${hist.map(historyRow).join("")}</tbody></table></div>`
        : '<div class="empty">開示履歴がありません。「この銘柄の2年分を取得」を実行してください。</div>'}
      </div>
      <div class="card">
        <h2>✏️ 分析コメント</h2>
        <textarea id="an_comment" style="min-height:140px" placeholder="この銘柄の分析メモを入力 (例: 増収増益が続く。来期ガイダンスは保守的…)">${h(comment)}</textarea>
        <div style="margin-top:8px;text-align:right"><button class="btn small" id="an_comment_save">保存</button></div>
        <div class="meta-line">コメントはこのブラウザに保存されます。</div>
      </div>
    </div>`;

  el("an_mode_a").onclick = () => { analysisView.mode = "a"; renderAnalysisBody(code); };
  el("an_mode_q").onclick = () => { analysisView.mode = "q"; renderAnalysisBody(code); };
  el("an_comment_save").onclick = () => {
    analysisState.comments[code] = el("an_comment").value;
    saveAnalysisState();
    toast("コメントを保存しました");
  };
  el("an_fetch_one").onclick = async () => {
    const btn = el("an_fetch_one");
    btn.disabled = true;
    try {
      const res = await bulkFetchHistory([code], el("an_one_status"));
      await renderAnalysisBody(code);
      const st = el("an_one_status");
      if (st) st.textContent = res.message;
    } finally {
      const btn2 = el("an_fetch_one");
      if (btn2) btn2.disabled = false;
    }
  };
}

// ---------------------------------------------------------------------------
// 銘柄比較タブ
// ---------------------------------------------------------------------------
route("compare", async (app) => {
  const my = await api.get("/mystocks");
  const quickAdd = my.items
    .filter((m) => !compareCodes.includes(m.code))
    .map((m) => `<button class="btn small ghost" data-add="${h(m.code)}">＋ ${h(m.code)} ${h(m.name)}</button>`)
    .join(" ");

  app.innerHTML = `
    <div class="page-head"><h1>銘柄比較分析</h1><span class="sub">最大4銘柄の業績・指標を並べて比較します</span></div>
    <div class="card" style="margin-bottom:14px">
      <div class="picker-bar">
        <input id="cmp_code" placeholder="銘柄コードを追加 (例: 7203)" style="width:180px">
        <button class="btn" id="cmp_add">追加</button>
        <span id="cmp_chips" style="display:inline-flex;gap:8px;flex-wrap:wrap">
          ${compareCodes.map((c) => `<span class="cmp-chip">${h(c)}<button data-del="${h(c)}" title="削除">✕</button></span>`).join("")}
        </span>
      </div>
      ${quickAdd ? `<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${quickAdd}</div>` : ""}
    </div>
    <div id="cmp_body"><div class="loading">読み込み中…</div></div>`;

  const addCode = async (c) => {
    c = String(c || "").trim();
    if (!c) return;
    if (compareCodes.includes(c)) { toast("既に追加されています", true); return; }
    if (compareCodes.length >= 4) { toast("比較は最大4銘柄までです", true); return; }
    try {
      await api.get("/stocks/" + encodeURIComponent(c)); // 存在チェック
    } catch (e) {
      toast(e.message, true);
      return;
    }
    compareCodes.push(c);
    saveCompareCodes();
    render();
  };
  el("cmp_add").onclick = () => addCode(el("cmp_code").value);
  el("cmp_code").addEventListener("keydown", (e) => { if (e.key === "Enter") addCode(el("cmp_code").value); });
  app.querySelectorAll("[data-add]").forEach((b) => { b.onclick = () => addCode(b.dataset.add); });
  app.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = () => {
      compareCodes = compareCodes.filter((c) => c !== b.dataset.del);
      saveCompareCodes();
      render();
    };
  });
  await renderCompareBody();
});

// ---------------------------------------------------------------------------
// スクリーナー (全銘柄を指標で絞り込み・ランキング)
// ---------------------------------------------------------------------------
const screenerState = {
  market: "", capMin: "", capMax: "", perMax: "", ncMin: "",
  roeMin: "", eqMin: "", cagrMin: "", divMin: "", sort: "nc", order: "desc",
};

const SCREENER_PRESETS = [
  { key: "kiyohara", label: "💰 清原式割安", desc: "ネットキャッシュ比率50%以上 × PER10倍以下",
    set: { ncMin: "50", perMax: "10", sort: "nc", order: "desc" } },
  { key: "growth", label: "🚀 高成長", desc: "売上CAGR(3年)15%以上",
    set: { cagrMin: "15", sort: "cagr", order: "desc" } },
  { key: "quality", label: "✨ 高ROE割安", desc: "ROE12%以上 × PER12倍以下",
    set: { roeMin: "12", perMax: "12", sort: "roe", order: "desc" } },
  { key: "income", label: "🪙 健全×高配当", desc: "自己資本比率50%以上 × 配当利回り3.5%以上",
    set: { eqMin: "50", divMin: "3.5", sort: "div", order: "desc" } },
];

let _allStocksCache = null;
async function loadAllStocks() {
  if (_allStocksCache) return _allStocksCache;
  try {
    const res = await fetch("data/stocks.json", { cache: "no-cache" });
    if (res.ok) {
      const j = await res.json();
      if (Array.isArray(j) && j.length) _allStocksCache = j;
    }
  } catch (e) { /* 実データなし */ }
  return _allStocksCache;
}

let _scrRows = null;
async function buildScreenerRows() {
  if (_scrRows) return _scrRows;
  const stocks = await loadAllStocks();
  if (!stocks) return null;
  const [findata, prices] = await Promise.all([loadFinancials(), loadPrices()]);
  const rows = [];
  for (const s of stocks) {
    const fin = findata.stocks[s.code] || {};
    const a = fin.a || [], b = fin.b || [];
    const la = a[a.length - 1], lb = b[b.length - 1];
    const cap = s.market_cap || null;
    const ni = la && la[3], rev = la && la[1];
    const nc = netCashInfo(lb, cap);
    const eq = lb && lb[2] != null && lb[4] != null ? lb[2] - lb[4] : null;
    const yrs = Math.min(3, a.length - 1);
    const p = prices.stocks[s.code];
    rows.push({
      code: s.code, name: s.name, market: s.market || "", sector: s.sector || "",
      cap,
      per: cap && ni > 0 ? cap / ni : null,
      psr: cap && rev > 0 ? cap / rev : null,
      nc: nc && nc.ratio != null ? nc.ratio : null,
      eqR: lb && lb[2] > 0 && eq != null ? (eq / lb[2]) * 100 : null,
      roe: ni != null && eq > 0 ? (ni / eq) * 100 : null,
      cagr: yrs >= 2 && la ? cagrPct(a[a.length - 1 - yrs][1], la[1], yrs) : null,
      div: p && p[6] != null ? p[6] : null,
      chg: p && p[1] != null ? p[1] : null,
    });
  }
  _scrRows = rows;
  return rows;
}

route("screener", async (app) => {
  const markets = await api.get("/markets");
  const st = screenerState;
  const field = (id, label, ph) => `<div class="field"><label>${label}</label>
    <input id="${id}" type="number" step="any" value="${h(String(st[id.slice(4)] || ""))}" placeholder="${ph}"></div>`;
  app.innerHTML = `
    <div class="page-head"><h1>スクリーナー</h1><span class="sub">全銘柄を財務指標で絞り込み・ランキングします</span></div>
    <div class="chips" id="scrPresets">
      ${SCREENER_PRESETS.map((p) => `<span class="chip" data-preset="${p.key}" title="${h(p.desc)}">${p.label}</span>`).join("")}
      <span class="chip" data-preset="" title="条件をクリア">クリア</span>
    </div>
    <div class="filters">
      <div class="field"><label>市場区分</label><select id="scr_market"><option value="">すべて</option>
        ${markets.markets.map((m) => `<option ${st.market === m ? "selected" : ""}>${h(m)}</option>`).join("")}</select></div>
      ${field("scr_capMin", "時価総額 下限(億円)", "例: 50")}
      ${field("scr_capMax", "時価総額 上限(億円)", "例: 1000")}
      ${field("scr_perMax", "PER 上限(倍)", "例: 10")}
      ${field("scr_ncMin", "ネットキャッシュ比率 下限(%)", "例: 50")}
      ${field("scr_roeMin", "ROE 下限(%)", "例: 12")}
      ${field("scr_eqMin", "自己資本比率 下限(%)", "例: 50")}
      ${field("scr_cagrMin", "売上CAGR 下限(%)", "例: 15")}
      ${field("scr_divMin", "配当利回り 下限(%)", "例: 3.5")}
      <div class="field" style="justify-content:flex-end"><label>&nbsp;</label>
        <button class="btn" id="scr_apply">絞り込む</button></div>
    </div>
    <div id="scr_result"><div class="loading">読み込み中…</div></div>`;

  const collect = () => {
    st.market = el("scr_market").value;
    for (const k of ["capMin", "capMax", "perMax", "ncMin", "roeMin", "eqMin", "cagrMin", "divMin"]) {
      st[k] = el("scr_" + k).value.trim();
    }
  };
  el("scr_apply").onclick = () => { collect(); renderScreenerResult(); };
  el("scrPresets").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    Object.assign(st, { market: "", capMin: "", capMax: "", perMax: "", ncMin: "",
      roeMin: "", eqMin: "", cagrMin: "", divMin: "", sort: "nc", order: "desc" });
    const preset = SCREENER_PRESETS.find((p) => p.key === c.dataset.preset);
    if (preset) Object.assign(st, preset.set);
    render();
  });
  await renderScreenerResult();
});

async function renderScreenerResult() {
  const box = el("scr_result");
  if (!box) return;
  box.innerHTML = '<div class="loading">計算中…</div>';
  const rows = await buildScreenerRows();
  if (!box.isConnected) return;
  if (!rows) {
    box.innerHTML = '<div class="empty">スクリーナーは実データ (frontend/data/) がある環境で利用できます。</div>';
    return;
  }
  const st = screenerState;
  const num = (v) => (v === "" || v == null ? null : Number(v));
  const capMin = num(st.capMin) != null ? num(st.capMin) * 1e8 : null;
  const capMax = num(st.capMax) != null ? num(st.capMax) * 1e8 : null;
  const perMax = num(st.perMax), ncMin = num(st.ncMin), roeMin = num(st.roeMin);
  const eqMin = num(st.eqMin), cagrMin = num(st.cagrMin), divMin = num(st.divMin);
  let items = rows.filter((r) =>
    (!st.market || r.market === st.market) &&
    (capMin == null || (r.cap != null && r.cap >= capMin)) &&
    (capMax == null || (r.cap != null && r.cap <= capMax)) &&
    (perMax == null || (r.per != null && r.per <= perMax)) &&
    (ncMin == null || (r.nc != null && r.nc >= ncMin)) &&
    (roeMin == null || (r.roe != null && r.roe >= roeMin)) &&
    (eqMin == null || (r.eqR != null && r.eqR >= eqMin)) &&
    (cagrMin == null || (r.cagr != null && r.cagr >= cagrMin)) &&
    (divMin == null || (r.div != null && r.div >= divMin)));
  const dir = st.order === "asc" ? 1 : -1;
  items.sort((x, y) => {
    const a = x[st.sort], b = y[st.sort];
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    return (a - b) * dir;
  });
  const total = items.length;
  items = items.slice(0, 300);
  const arrow = (col) => st.sort === col ? (st.order === "asc" ? " ▲" : " ▼") : "";
  const pctTd = (v, goodTh) => v == null ? '<td class="num">-</td>'
    : `<td class="num ${goodTh != null && v >= goodTh ? "pos" : ""}">${v.toFixed(1)}%</td>`;
  const chgTd = (v) => v == null ? '<td class="num">-</td>'
    : `<td class="num ${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${v.toFixed(2)}%</td>`;
  box.innerHTML = `
    <div class="page-head"><span class="sub">${total.toLocaleString("en-US")}件ヒット${total > 300 ? " (上位300件を表示)" : ""}</span></div>
    ${total === 0 ? '<div class="empty">条件に一致する銘柄はありません</div>' : `
    <div class="table-wrap"><table>
      <thead><tr>
        <th class="no-sort">コード</th><th class="no-sort">銘柄名</th><th class="no-sort">市場</th>
        <th data-sort="cap">時価総額${arrow("cap")}</th>
        <th data-sort="per">PER${arrow("per")}</th>
        <th data-sort="psr">PSR${arrow("psr")}</th>
        <th data-sort="nc" title="ネットキャッシュ比率 (清原式)">NC比率${arrow("nc")}</th>
        <th data-sort="eqR">自己資本${arrow("eqR")}</th>
        <th data-sort="roe">ROE${arrow("roe")}</th>
        <th data-sort="cagr">売上CAGR${arrow("cagr")}</th>
        <th data-sort="div">配当${arrow("div")}</th>
        <th data-sort="chg">前日比${arrow("chg")}</th>
      </tr></thead>
      <tbody>${items.map((r) => `<tr>
        <td class="code-cell"><a class="link" href="#/analysis/${h(r.code)}">${h(r.code)}</a></td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${h(r.name)} / ${h(r.sector)}">${h(r.name)}</td>
        <td><span class="badge market">${h(r.market)}</span></td>
        <td class="num">${r.cap != null ? fmtMoney(r.cap) : "-"}</td>
        <td class="num">${r.per != null ? r.per.toFixed(1) + "倍" : "-"}</td>
        <td class="num">${r.psr != null ? r.psr.toFixed(2) + "倍" : "-"}</td>
        ${pctTd(r.nc, 50)}${pctTd(r.eqR)}${pctTd(r.roe, 12)}${pctTd(r.cagr, 15)}
        <td class="num">${r.div != null ? r.div.toFixed(2) + "%" : "-"}</td>
        ${chgTd(r.chg)}
      </tr>`).join("")}</tbody>
    </table></div>
    <div class="meta-line" style="margin-top:8px">PER・PSR・ROE・NC比率は直近通期実績と最新時価総額からの概算。財務データ (BS) 未取得の銘柄は該当指標が「-」となり、指標フィルタ指定時は除外されます。銘柄コードから銘柄分析へ移動できます。</div>`}`;
  box.querySelectorAll("th[data-sort]").forEach((th) => {
    th.onclick = () => {
      const col = th.dataset.sort;
      if (st.sort === col) st.order = st.order === "asc" ? "desc" : "asc";
      else { st.sort = col; st.order = "desc"; }
      renderScreenerResult();
    };
  });
}

// ---------------------------------------------------------------------------
// 市況概況タブ (当日の市場全体の状況 + 日次スナップショットによる傾向)
//
// GitHub Actions (scripts/market_summary.py) が毎営業日の引け後に
// data/market/YYYY-MM-DD.json (この画面と同じ形の統計) と .md (生成AI向け) を
// 保存する。当日は prices.json 等から同じ形の統計をその場で計算して表示する。
// ---------------------------------------------------------------------------
const marketState = { date: "" }; // "" = 最新 (当日データから計算)

let _mktIndexCache = null;
async function loadMarketIndex() {
  if (_mktIndexCache) return _mktIndexCache;
  try {
    const res = await fetch("data/market/index.json", { cache: "no-cache" });
    if (res.ok) {
      const j = await res.json();
      if (j && Array.isArray(j.series)) { _mktIndexCache = j; return j; }
    }
  } catch (e) { /* 未生成 */ }
  _mktIndexCache = { dates: [], series: [] };
  return _mktIndexCache;
}

// scripts/market_summary.py compute_stats と同じ形の統計オブジェクトを作る
function computeMarketStats(stocks, prices, reactions, sched, discs) {
  const rows = [];
  for (const s of stocks) {
    const p = prices.stocks[s.code];
    if (!p) continue;
    const close = p[0], chg = p[1], hi = p[2], lo = p[3], vol = p[4], avg = p[5];
    rows.push({
      code: s.code, name: s.name, market: s.market || "", sector: s.sector || "",
      cap: s.market_cap || null, close, chg, hi, lo, vol, avg,
      value: close != null && vol != null ? close * vol : null,
      impact: s.market_cap && chg != null ? s.market_cap - s.market_cap / (1 + chg / 100) : null,
    });
  }
  const withChg = rows.filter((r) => r.chg != null);
  const up = withChg.filter((r) => r.chg > 0.0001).length;
  const down = withChg.filter((r) => r.chg < -0.0001).length;
  const avgChg = withChg.length ? withChg.reduce((s, r) => s + r.chg, 0) / withChg.length : null;
  const capSum = withChg.reduce((s, r) => s + (r.cap || 0), 0);
  const wAvg = capSum ? withChg.reduce((s, r) => s + (r.cap || 0) * r.chg, 0) / capSum : null;

  // 騰落レシオ (終値履歴から最大25営業日)
  const tail = reactions.tail || { dates: [], closes: {} };
  let ratio = null, ratioDays = 0;
  if (tail.dates.length >= 2) {
    let adv = 0, dec = 0;
    const start = Math.max(1, tail.dates.length - 25);
    for (let i = start; i < tail.dates.length; i++) {
      for (const code in tail.closes) {
        const row = tail.closes[code];
        const a = row[i - 1], b = row[i];
        if (a != null && b != null) {
          if (b > a) adv++;
          else if (b < a) dec++;
        }
      }
      ratioDays++;
    }
    if (dec > 0) ratio = (adv / dec) * 100;
  }

  const hi52 = rows.filter((r) => r.close != null && r.hi != null && r.close >= r.hi)
    .sort((a, b) => (b.cap || 0) - (a.cap || 0));
  const lo52 = rows.filter((r) => r.close != null && r.lo != null && r.close <= r.lo)
    .sort((a, b) => (b.cap || 0) - (a.cap || 0));
  const impacts = rows.filter((r) => r.impact != null).sort((a, b) => b.impact - a.impact);

  const secMap = new Map();
  for (const r of withChg) {
    if (!r.sector) continue;
    const m = secMap.get(r.sector) || { cap: 0, wsum: 0, n: 0, sum: 0 };
    const w = r.cap || 0;
    m.cap += w; m.wsum += w * r.chg; m.n++; m.sum += r.chg;
    secMap.set(r.sector, m);
  }
  const sectors = [...secMap.entries()]
    .map(([name, m]) => ({ name, chg: m.cap ? m.wsum / m.cap : m.sum / m.n, n: m.n }))
    .sort((a, b) => b.chg - a.chg);

  const segments = ["プライム", "スタンダード", "グロース"].map((seg) => {
    const rs = withChg.filter((r) => r.market === seg);
    const cs = rs.reduce((s, r) => s + (r.cap || 0), 0);
    return { seg, n: rs.length, chg: cs ? rs.reduce((s, r) => s + (r.cap || 0) * r.chg, 0) / cs : null };
  });

  const byDay = new Map();
  for (const it of (sched.items || [])) byDay.set(it.announce_date, (byDay.get(it.announce_date) || 0) + 1);

  const discToday = {};
  const today = prices.date || "";
  for (const x of (discs.items || [])) {
    if ((x.published_at || "").slice(0, 10) === today && x.doc_type) {
      discToday[x.doc_type] = (discToday[x.doc_type] || 0) + 1;
    }
  }

  const brief = (r) => ({ code: r.code, name: r.name, close: r.close, chg: r.chg });
  const stats = {
    date: prices.date || "",
    summary: {
      total: withChg.length, up, down, flat: withChg.length - up - down,
      avg: avgChg, wavg: wAvg,
      ratio, ratio_days: ratioDays,
      ratio_judge: ratio == null ? null : ratio >= 120 ? "過熱気味" : ratio <= 70 ? "売られすぎ圏" : "中立圏",
      surge: withChg.filter((r) => r.chg >= 8).length,
      plunge: withChg.filter((r) => r.chg <= -8).length,
      hi52_count: hi52.length, lo52_count: lo52.length,
      value_total: rows.reduce((s, r) => s + (r.value || 0), 0),
      cap_total: capSum,
      cap_change: rows.reduce((s, r) => s + (r.impact || 0), 0),
      sectors_up: sectors.filter((x) => x.chg > 0).length,
      sectors_down: sectors.filter((x) => x.chg < 0).length,
    },
    sectors, segments,
    rank_value: rows.filter((r) => r.value != null).sort((a, b) => b.value - a.value)
      .slice(0, 15).map((r) => Object.assign(brief(r), { value: r.value })),
    gainers: [...withChg].sort((a, b) => b.chg - a.chg).slice(0, 10).map(brief),
    losers: [...withChg].sort((a, b) => a.chg - b.chg).slice(0, 10).map(brief),
    hi52: hi52.slice(0, 10).map(brief),
    lo52: lo52.slice(0, 10).map(brief),
    impact_pos: impacts.filter((r) => r.impact > 0).slice(0, 8)
      .map((r) => Object.assign(brief(r), { impact: r.impact })),
    impact_neg: impacts.filter((r) => r.impact < 0).slice(-8).reverse()
      .map((r) => Object.assign(brief(r), { impact: r.impact })),
    vol_spike: rows.filter((r) => r.vol && r.avg && r.vol >= r.avg * 5)
      .sort((a, b) => b.vol / b.avg - a.vol / a.avg).slice(0, 10)
      .map((r) => Object.assign(brief(r), { x: r.vol / r.avg })),
    earnings_week: [...byDay.entries()].sort(),
    disclosures_today: discToday,
    comment: "",
  };
  return stats;
}

// 主要指標の推移チャート (index.json の時系列から)
function marketTrendHtml(idx) {
  const series = idx.series || [];
  if (series.length < 2) {
    return `<div class="card" style="margin-bottom:16px">
      <h2>📈 市況の推移</h2>
      <div class="empty">市況スナップショットは毎営業日の引け後に自動保存されます (現在${series.length}日分)。
      2日分以上たまると、騰落レシオ・値上がり銘柄数・新高値/新安値などの推移チャートがここに表示されます。
      保存されたMD/JSONは <code>frontend/data/market/</code> にあり、MDは生成AIにそのまま渡して傾向を質問できます。</div>
    </div>`;
  }
  const labels = series.map((r) => String(r[0]).slice(5).replace("-", "/"));
  const col = (i) => series.map((r) => (r[i] == null ? null : r[i]));
  return `<div class="card" style="margin-bottom:16px">
    <h2>📈 市況の推移 <span class="count">${series.length}営業日分の日次スナップショット</span></h2>
    <div class="charts-grid">
      ${chartSVG({ title: "騰落レシオ", labels, series: [{ name: "騰落レシオ(%)", color: CHART_COLORS[4], values: col(3), type: "line" }] })}
      ${chartSVG({ title: "値上がり・値下がり銘柄数", labels, series: [
        { name: "値上がり", color: CHART_COLORS[1], values: col(1), type: "line" },
        { name: "値下がり", color: "#ef4444", values: col(2), type: "line" }] })}
      ${chartSVG({ title: "平均騰落率 (時価総額加重)", labels, unit: "pct", series: [{ name: "加重騰落率(%)", color: CHART_COLORS[0], values: col(4), type: "line" }] })}
      ${chartSVG({ title: "52週高値・安値の更新数", labels, series: [
        { name: "高値更新", color: CHART_COLORS[1], values: col(5), type: "line" },
        { name: "安値更新", color: "#ef4444", values: col(6), type: "line" }] })}
    </div>
    <div class="meta-line">日次スナップショット (JSON/MD) は <code>frontend/data/market/</code> に保存されています。騰落レシオ120%超は過熱、70%未満は底値圏の目安。</div>
  </div>`;
}

function renderMarketHtml(st) {
  const s = st.summary || {};
  const chgSpan = (v, digits) => v == null ? "-"
    : `<span style="color:${v >= 0 ? "#4ade80" : "#f87171"}">${v >= 0 ? "+" : ""}${v.toFixed(digits == null ? 2 : digits)}%</span>`;
  const stat = (label, value, sub) => `<div class="card stat"><div class="label">${label}</div>
    <div class="value" style="font-size:22px">${value}</div>${sub ? `<div class="meta-line" style="margin-top:2px">${sub}</div>` : ""}</div>`;
  const nameTd = (r) => `<td class="code-cell"><a class="link" href="#/analysis/${h(r.code)}">${h(r.code)}</a></td>
    <td style="max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${h(r.name)}">${h(r.name)}</td>`;
  const rankTable = (list, cols, headers) => `<div class="table-wrap"><table>
    <thead><tr><th>コード</th><th>銘柄名</th>${headers.map((x) => `<th>${x}</th>`).join("")}</tr></thead>
    <tbody>${(list || []).map((r) => `<tr>${nameTd(r)}${cols(r)}</tr>`).join("")}</tbody></table></div>`;
  const closeTd = (r) => `<td class="num">${r.close != null ? Number(r.close).toLocaleString("en-US") + "円" : "-"}</td>`;
  const fmtOku = (v) => v == null ? "-" : fmtMoney(v);
  const num = (v) => (v == null ? "-" : Number(v).toLocaleString("en-US"));

  const sectors = st.sectors || [];
  const secMax = Math.max(0.01, ...sectors.map((x) => Math.abs(x.chg)));
  const sectorBars = sectors.map((x) => {
    const w = Math.abs(x.chg) / secMax * 100;
    const color = x.chg >= 0 ? "#22c55e" : "#ef4444";
    return `<div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:12px">
      <span style="width:110px;flex:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${h(x.name)} (${x.n}銘柄)">${h(x.name)}</span>
      <span style="width:64px;flex:none;text-align:right;color:${color}">${x.chg >= 0 ? "+" : ""}${x.chg.toFixed(2)}%</span>
      <span style="flex:1;height:10px;background:var(--bg-elev);border-radius:4px;overflow:hidden">
        <span style="display:block;height:100%;width:${w.toFixed(1)}%;background:${color};opacity:.8"></span></span>
    </div>`;
  }).join("");

  const days = st.earnings_week || [];
  const dayMax = Math.max(1, ...days.map((x) => x[1]));
  const weekTotal = days.reduce((sm, x) => sm + x[1], 0);
  const discToday = st.disclosures_today || {};
  const discBadges = Object.keys(discToday).length
    ? Object.entries(discToday).sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<span class="badge market" style="font-size:12px;padding:4px 10px">${h(k)} ${v}件</span>`).join(" ")
    : '<span class="meta-line">本日の決算関連の開示はまだありません</span>';
  const ratioJudge = s.ratio_judge
    ? `<span class="badge ${s.ratio >= 120 ? "unread" : s.ratio <= 70 ? "ok" : "market"}">${h(s.ratio_judge)}</span> 120%超=過熱 / 70%未満=底値圏`
    : "終値履歴の蓄積後に25日で算出";

  return `
    ${st.comment ? `<div class="card" style="margin-bottom:16px">
      <h2>📝 概況コメント <span class="count">自動生成</span></h2>
      <div style="font-size:13px;line-height:1.9">${h(st.comment)}</div>
    </div>` : ""}

    <div class="grid cols-4" style="margin-bottom:12px;grid-template-columns:repeat(5,1fr)">
      ${stat("値上がり / 値下がり", `<span style="color:#4ade80">${num(s.up)}</span> / <span style="color:#f87171">${num(s.down)}</span>`, `変わらず ${num(s.flat)} / 全${num(s.total)}銘柄`)}
      ${stat("平均騰落率 (単純)", chgSpan(s.avg), "全銘柄の単純平均")}
      ${stat("平均騰落率 (加重)", chgSpan(s.wavg), "時価総額加重 (大型株の影響大)")}
      ${stat(`騰落レシオ (${s.ratio_days || "-"}日)`, s.ratio == null ? "蓄積中" : s.ratio.toFixed(0) + "%", ratioJudge)}
      ${stat("急騰 / 急落 (±8%)", `<span style="color:#4ade80">${num(s.surge)}</span> / <span style="color:#f87171">${num(s.plunge)}</span>`, "銘柄数")}
    </div>
    <div class="grid cols-4" style="margin-bottom:16px;grid-template-columns:repeat(5,1fr)">
      ${stat("概算売買代金", fmtOku(s.value_total), "終値×出来高の合計")}
      ${stat("時価総額合計", fmtOku(s.cap_total), `前日比 <span style="color:${(s.cap_change || 0) >= 0 ? "#4ade80" : "#f87171"}">${(s.cap_change || 0) >= 0 ? "+" : ""}${fmtOku(s.cap_change)}</span>`)}
      ${stat("上昇 / 下落業種", `<span style="color:#4ade80">${num(s.sectors_up)}</span> / <span style="color:#f87171">${num(s.sectors_down)}</span>`, "東証33業種")}
      ${stat("ネット新高値", `${(s.hi52_count || 0) - (s.lo52_count || 0) >= 0 ? "+" : ""}${(s.hi52_count || 0) - (s.lo52_count || 0)}`, `52週高値${num(s.hi52_count)} − 安値${num(s.lo52_count)}`)}
      ${stat("今週の決算発表", num(weekTotal) + "件", '日別の内訳は下部に表示')}
    </div>

    <div class="card" style="margin-bottom:16px">
      <h2>📢 本日の開示</h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${discBadges}</div>
    </div>

    <div class="grid cols-2" style="margin-bottom:16px">
      <div class="card">
        <h2>🏭 セクター別騰落 <span class="count">33業種・時価総額加重</span></h2>
        ${sectorBars || '<div class="empty">データなし</div>'}
      </div>
      <div class="card">
        <h2>💹 売買代金ランキング <span class="count">概算 (終値×出来高)</span></h2>
        ${rankTable(st.rank_value, (r) => `<td class="num">${fmtOku(r.value)}</td><td class="num">${chgSpan(r.chg)}</td>`, ["売買代金", "前日比"])}
      </div>
    </div>

    <div class="grid cols-2" style="margin-bottom:16px">
      <div class="card">
        <h2>📈 値上がり率ランキング</h2>
        ${rankTable(st.gainers, (r) => `<td class="num">${chgSpan(r.chg)}</td>${closeTd(r)}`, ["前日比", "終値"])}
      </div>
      <div class="card">
        <h2>📉 値下がり率ランキング</h2>
        ${rankTable(st.losers, (r) => `<td class="num">${chgSpan(r.chg)}</td>${closeTd(r)}`, ["前日比", "終値"])}
      </div>
    </div>

    <div class="grid cols-2" style="margin-bottom:16px">
      <div class="card">
        <h2>🚀 52週高値更新 <span class="count">${num(s.hi52_count)}銘柄</span></h2>
        ${(st.hi52 || []).length ? rankTable(st.hi52, (r) => `${closeTd(r)}<td class="num">${chgSpan(r.chg)}</td>`, ["終値", "前日比"]) : '<div class="empty">本日の更新はありません</div>'}
        <div class="meta-line">時価総額の大きい順に表示。データソースの制約で年初来・上場来高値は52週高値で代替しています。</div>
      </div>
      <div class="card">
        <h2>🔻 52週安値更新 <span class="count">${num(s.lo52_count)}銘柄</span></h2>
        ${(st.lo52 || []).length ? rankTable(st.lo52, (r) => `${closeTd(r)}<td class="num">${chgSpan(r.chg)}</td>`, ["終値", "前日比"]) : '<div class="empty">本日の更新はありません</div>'}
      </div>
    </div>

    <div class="grid cols-2" style="margin-bottom:16px">
      <div class="card">
        <h2>⚖️ 指数インパクト <span class="count">時価総額の増減額 (指数寄与の概算)</span></h2>
        <div class="grid cols-2">
          <div>${rankTable(st.impact_pos, (r) => `<td class="num pos">+${fmtOku(r.impact)}</td>`, ["増加額"])}</div>
          <div>${rankTable(st.impact_neg, (r) => `<td class="num neg">${fmtOku(r.impact)}</td>`, ["減少額"])}</div>
        </div>
        <div class="meta-line">全銘柄の時価総額変化額。日経平均は株価加重のため厳密な寄与度とは異なりますが、市場を動かした銘柄の把握に使えます。</div>
      </div>
      <div class="card">
        <h2>📊 出来高急増 <span class="count">3ヶ月平均の5倍以上</span></h2>
        ${(st.vol_spike || []).length ? rankTable(st.vol_spike, (r) => `<td class="num">${(r.x || 0).toFixed(1)}倍</td><td class="num">${chgSpan(r.chg)}</td>`, ["出来高倍率", "前日比"]) : '<div class="empty">該当なし</div>'}
      </div>
    </div>

    <div class="grid cols-2">
      <div class="card">
        <h2>🏛 市場区分別騰落 <span class="count">時価総額加重</span></h2>
        ${(st.segments || []).map((x) => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px">
          <span>${h(x.seg)} <span class="count">${num(x.n)}銘柄</span></span><span>${chgSpan(x.chg)}</span></div>`).join("")}
      </div>
      <div class="card">
        <h2>📅 今週の決算発表 <span class="count">${num(weekTotal)}件</span></h2>
        ${days.length ? days.map((x) => `<div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:12px">
          <span style="width:70px;flex:none">${fmtDate(x[0])}</span>
          <span style="width:50px;flex:none;text-align:right">${x[1]}件</span>
          <span style="flex:1;height:10px;background:var(--bg-elev);border-radius:4px;overflow:hidden">
            <span style="display:block;height:100%;width:${(x[1] / dayMax * 100).toFixed(1)}%;background:var(--accent);opacity:.8"></span></span>
        </div>`).join("") : '<div class="empty">今週の決算予定はありません</div>'}
        <div class="meta-line">決算集中日は値動きが大きくなりやすい点に注意。<a class="link" href="#/schedule">決算予定タブ</a>で詳細を確認できます。</div>
      </div>
    </div>`;
}

route("market", async (app) => {
  const idx = await loadMarketIndex();
  const dateOpts = [...(idx.dates || [])].reverse();
  app.innerHTML = `
    <div class="page-head"><h1>市況概況</h1><span class="sub" id="mkt_date"></span>
      <span style="margin-left:auto;display:inline-flex;gap:8px;align-items:center">
        <select id="mkt_sel" style="width:auto" title="保存済みの過去の市況を表示">
          <option value="">最新 (本日)</option>
          ${dateOpts.map((d) => `<option value="${h(d)}" ${marketState.date === d ? "selected" : ""}>${h(d)}</option>`).join("")}
        </select>
        <a class="btn small ghost" id="mkt_md" target="_blank" rel="noopener" style="display:none" title="生成AIに渡しやすいMarkdown形式のレポートを開く">📝 MDレポート</a>
      </span></div>
    <div id="mkt_trend"></div>
    <div id="mkt_body"><div class="loading">読み込み中…</div></div>`;
  el("mkt_sel").onchange = () => {
    marketState.date = el("mkt_sel").value;
    render();
  };
  el("mkt_trend").innerHTML = marketTrendHtml(idx);

  const body = el("mkt_body");
  let stats = null;
  if (marketState.date) {
    try {
      const res = await fetch("data/market/" + marketState.date + ".json", { cache: "no-cache" });
      if (res.ok) stats = await res.json();
    } catch (e) { /* 下でエラー表示 */ }
    if (!stats) {
      if (body.isConnected) body.innerHTML = '<div class="empty">この日のスナップショットを読み込めませんでした。</div>';
      return;
    }
  } else {
    const [stocks, prices, reactions, sched, discs] = await Promise.all([
      loadAllStocks(), loadPrices(), loadReactions(),
      api.get("/schedule?date_range=this_week"), api.get("/disclosures"),
    ]);
    if (!stocks || !Object.keys(prices.stocks || {}).length) {
      if (body.isConnected) body.innerHTML = '<div class="empty">市況概況は実データ (frontend/data/) がある環境で利用できます。</div>';
      return;
    }
    stats = computeMarketStats(stocks, prices, reactions, sched, discs);
    // 当日分のスナップショットが保存済みなら、概況コメントを取り込む
    if ((idx.dates || []).includes(stats.date)) {
      try {
        const res = await fetch("data/market/" + stats.date + ".json", { cache: "no-cache" });
        if (res.ok) stats.comment = ((await res.json()) || {}).comment || "";
      } catch (e) { /* 無くてもよい */ }
    }
  }
  if (!body.isConnected) return;
  const dEl = el("mkt_date");
  if (dEl && stats.date) dEl.textContent = `${fmtDate(stats.date)} 終値時点${marketState.date ? " (保存版)" : " (平日1日5回更新)"}`;
  if ((idx.dates || []).includes(stats.date)) {
    const a = el("mkt_md");
    a.href = "data/market/" + stats.date + ".md";
    a.style.display = "";
  }
  body.innerHTML = renderMarketHtml(stats);
});

// 決算シグナル + 投資指標カード (銘柄分析タブ)
function analysisInsightHtml(stock, fin, price) {
  const a = fin.a || [];
  const n = a.length;
  const last = a[n - 1];
  const signals = buildSignals(a);

  const rev = last && last[1], op = last && last[2], ni = last && last[3];
  const cap = stock.market_cap;
  const per = cap && ni > 0 ? cap / ni : null;
  const psr = cap && rev > 0 ? cap / rev : null;
  const nim = rev && ni != null ? (ni / rev) * 100 : null;
  const yrs = Math.min(3, n - 1);
  const revCagr = yrs >= 2 ? cagrPct(a[n - 1 - yrs][1], last[1], yrs) : null;
  const opCagr = yrs >= 2 ? cagrPct(a[n - 1 - yrs][2], last[2], yrs) : null;

  // ROE・ROA (BS) と利益の質 (営業CF vs 純利益)
  const b = fin.b || [], c = fin.c || [];
  const lastB = b[b.length - 1], lastC = c[c.length - 1];
  const eq = lastB && lastB[2] != null && lastB[4] != null ? lastB[2] - lastB[4] : null;
  const roe = ni != null && eq > 0 ? (ni / eq) * 100 : null;
  const roa = ni != null && lastB && lastB[2] > 0 ? (ni / lastB[2]) * 100 : null;
  const ocf = lastC && lastC[1];
  const cfRatio = ni > 0 && ocf != null ? ocf / ni : null;
  if (ni > 0 && ocf != null && ocf < ni * 0.5) {
    signals.push({ label: ocf < 0 ? "営業CFが赤字 (利益の質に注意)" : "営業CF < 純利益の半分 (利益の質に注意)", good: false });
  }
  const divY = price && price[6] != null ? price[6] : null;

  const sigHtml = signals.length
    ? signals.map((s) => `<span class="badge ${s.good ? "ok" : "unread"}" style="font-size:12px;padding:4px 12px">${h(s.label)}</span>`).join(" ")
    : '<span class="meta-line">判定に必要な期数が不足しています</span>';
  const kv = (label, v) => `<div class="card stat"><div class="label">${label}</div><div class="value" style="font-size:18px">${v}</div></div>`;
  const pct = (v) => (v == null ? "-" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%");

  return `
    <div class="card" style="margin-bottom:14px">
      <h2>🔎 決算シグナル <span class="count">(直近通期 ${last ? h(periodLabel(last[0])) + "期" : ""} vs 前期)</span></h2>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${sigHtml}</div>
    </div>
    <div class="grid cols-4" style="margin-bottom:14px;grid-template-columns:repeat(5,1fr)">
      ${kv("PER (概算)", per == null ? "-" : per.toFixed(1) + "倍")}
      ${kv("PSR (概算)", psr == null ? "-" : psr.toFixed(2) + "倍")}
      ${kv("純利益率", nim == null ? "-" : nim.toFixed(1) + "%")}
      ${kv(`売上CAGR (${yrs}年)`, pct(revCagr))}
      ${kv(`営業利益CAGR (${yrs}年)`, pct(opCagr))}
      ${kv("ROE", roe == null ? "-" : roe.toFixed(1) + "%")}
      ${kv("ROA", roa == null ? "-" : roa.toFixed(1) + "%")}
      ${kv("配当利回り", divY == null ? "-" : divY.toFixed(2) + "%")}
      ${kv("営業CF ÷ 純利益", cfRatio == null ? "-" : cfRatio.toFixed(2) + "倍")}
      ${kv("自己資本比率", lastB && lastB[2] > 0 && eq != null ? ((eq / lastB[2]) * 100).toFixed(1) + "%" : "-")}
    </div>`;
}

// ---- 決算進捗率 (季節性調整つき・概算) ----
// 会社予想データは無いため「対 前期通期」の消化率を、前年同時点の消化率と比較する。
function progressInfo(fin) {
  const a = fin.a || [], q = fin.q || [];
  if (a.length < 2 || !q.length) return null;
  const lastA = a[a.length - 1], prevA = a[a.length - 2];
  const curQ = q.filter((r) => r[0] > lastA[0]);            // 今期の四半期
  const n = curQ.length;
  if (!n) return null;
  const prevQ = q.filter((r) => r[0] > prevA[0] && r[0] <= lastA[0]).slice(0, n); // 前年同時点
  const metric = (idx, label) => {
    const sum = (rows) => {
      let s = 0;
      for (const r of rows) {
        if (r[idx] == null) return null;
        s += r[idx];
      }
      return s;
    };
    const cur = sum(curQ), base = lastA[idx];
    if (cur == null || base == null || base <= 0) return null;
    const curPct = (cur / base) * 100;
    let prevPct = null;
    if (prevQ.length === n) {
      const pcum = sum(prevQ), pbase = prevA[idx];
      if (pcum != null && pbase != null && pbase > 0) prevPct = (pcum / pbase) * 100;
    }
    return { label, curPct, prevPct, diff: prevPct == null ? null : curPct - prevPct };
  };
  const rows = [metric(1, "売上高"), metric(2, "営業利益"), metric(3, "純利益")].filter(Boolean);
  return rows.length ? { n, rows } : null;
}

function progressHtml(fin) {
  const p = progressInfo(fin);
  if (!p) return "";
  const cell = (m) => {
    let badge = "";
    if (m.diff != null) {
      const cls = m.diff >= 3 ? "ok" : m.diff <= -3 ? "unread" : "market";
      const word = m.diff >= 3 ? "先行" : m.diff <= -3 ? "遅れ" : "例年並み";
      badge = `<span class="badge ${cls}" style="margin-left:6px">${word} ${m.diff >= 0 ? "+" : ""}${m.diff.toFixed(1)}pt</span>`;
    }
    return `<div class="card stat" style="background:var(--bg-elev)">
      <div class="label">${h(m.label)}の進捗</div>
      <div class="value" style="font-size:20px">${m.curPct.toFixed(1)}%${badge}</div>
      <div class="meta-line" style="margin-top:2px">前年同時点 ${m.prevPct == null ? "-" : m.prevPct.toFixed(1) + "%"}</div>
    </div>`;
  };
  return `
    <div class="card" style="margin-bottom:14px">
      <h2>⏱ 決算進捗 <span class="count">第${p.n}四半期累計 ÷ 前期通期 (概算)</span></h2>
      <div class="grid cols-4" style="grid-template-columns:repeat(3,1fr)">${p.rows.map(cell).join("")}</div>
      <div class="meta-line">会社予想は取得していないため「前期通期に対する消化率」を前年同時点と比較しています。+3pt以上で「先行」、-3pt以下で「遅れ」。季節性 (上期偏重など) はこの比較で吸収されます。</div>
    </div>`;
}

// ---- イベントと株価反応 (reactions.json) ----
function reactionHtml(code, reactions) {
  const evs = (reactions.events || []).filter((e) => e.code === code);
  if (!evs.length) {
    return `<div class="card" style="margin-bottom:14px">
      <h2>🎯 イベントと株価反応</h2>
      <div class="empty">まだ記録がありません。決算発表・業績修正などの開示や急変動が発生すると、当日と翌営業日の株価反応がここに蓄積されていきます (毎営業日の引け後に自動記録)。</div>
    </div>`;
  }
  const pctCell = (v) => v == null ? '<td class="num">記録待ち</td>'
    : `<td class="num ${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</td>`;
  const rows = evs.slice(0, 15).map((e) => `<tr>
    <td>${fmtDate(e.d)}</td>
    <td><span class="badge market">${h(e.t)}</span></td>
    <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${h(e.l || "")}">${h(e.l || "")}</td>
    ${pctCell(e.c1)}${pctCell(e.c2)}
  </tr>`).join("");
  // 決算発表への反応の平均 (記録があるもののみ)
  const earn = evs.filter((e) => (e.t === "決算短信" || e.t === "訂正決算短信") && e.c1 != null);
  let summary = "";
  if (earn.length) {
    const avg = (key) => {
      const vs = earn.map((e) => e[key]).filter((v) => v != null);
      return vs.length ? vs.reduce((s, v) => s + v, 0) / vs.length : null;
    };
    const a1 = avg("c1"), a2 = avg("c2");
    const f = (v) => v == null ? "-" : `<span style="color:${v >= 0 ? "#4ade80" : "#f87171"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</span>`;
    summary = `<div class="meta-line" style="margin-bottom:8px">この銘柄の決算発表への平均反応 (${earn.length}回): 当日 ${f(a1)} / 翌営業日 ${f(a2)} — 好決算でも売られる癖などの参考に。</div>`;
  }
  return `
    <div class="card" style="margin-bottom:14px">
      <h2>🎯 イベントと株価反応 <span class="count">${evs.length}件記録</span></h2>
      ${summary}
      <div class="table-wrap"><table>
        <thead><tr><th>反応日</th><th>イベント</th><th>内容</th><th>当日</th><th>翌営業日</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="meta-line">15時以降の開示は翌営業日を「当日」として扱います。株価イベント (急変動・52週高安・出来高急増) は全銘柄で記録し、開示イベントは決算短信・業績予想修正・配当予想修正・自己株式取得を対象にしています。</div>
    </div>`;
}

// ---- 財務健全性 (BS/CF) 分析 ----
// financials.json の b系列: [期末日, 流動資産, 総資産, 流動負債, 負債合計, 株主資本, 投資有価証券]
//                 c系列: [期末日, 営業CF, 投資CF, 財務CF]

// ネットキャッシュ比率 (清原達郎氏の定義):
//   ネットキャッシュ = 流動資産 + 投資有価証券×70% − 負債合計
//   ネットキャッシュ比率 = ネットキャッシュ ÷ 時価総額
function netCashInfo(bRow, marketCap) {
  if (!bRow) return null;
  const curA = bRow[1], totL = bRow[4], inv = bRow[6];
  if (curA == null || totL == null) return null;
  const netCash = curA + (inv || 0) * 0.7 - totL;
  const ratio = marketCap ? (netCash / marketCap) * 100 : null;
  let judge, cls;
  if (ratio == null) { judge = "時価総額不明"; cls = "market"; }
  else if (ratio >= 100) { judge = "超割安水準 (時価総額 < ネットキャッシュ)"; cls = "ok"; }
  else if (ratio >= 50) { judge = "かなり割安 (清原式の目安圏)"; cls = "ok"; }
  else if (ratio >= 0) { judge = "ネットキャッシュ・プラス"; cls = "market"; }
  else { judge = "ネットデット (実質負債超過)"; cls = "unread"; }
  return { netCash, ratio, inv: inv || 0, judge, cls, hasInv: inv != null };
}

// BS構成の箱グラフ (左: 資産 = 流動+固定 / 右: 流動負債+固定負債+純資産)
function bsBoxSVG(bRow) {
  const curA = bRow[1], totA = bRow[2], curL = bRow[3], totL = bRow[4];
  if (totA == null || totA <= 0 || curA == null || curL == null || totL == null) return "";
  const fixA = totA - curA;
  const fixL = totL - curL;
  const eq = totA - totL; // 純資産 (少数株主持分含む)
  const W = 560, H = 260, PT = 8, PB = 26, colW = 190, gap = 40, x1 = 60, x2 = x1 + colW + gap;
  const scaleMax = Math.max(totA, totL);
  const ih = H - PT - PB;
  const hOf = (v) => Math.max(0, (v / scaleMax) * ih);
  const pct = (v) => ((v / totA) * 100).toFixed(0) + "%";
  let g = "";
  const seg = (x, yTop, hgt, color, label, value) => {
    g += `<rect x="${x}" y="${yTop.toFixed(1)}" width="${colW}" height="${Math.max(1.5, hgt).toFixed(1)}" fill="${color}" rx="3" opacity="0.88"/>`;
    if (hgt >= 30) {
      g += `<text x="${x + colW / 2}" y="${(yTop + hgt / 2 - 3).toFixed(1)}" text-anchor="middle" fill="#fff" font-size="12" font-weight="700">${label} ${pct(value)}</text>`;
      g += `<text x="${x + colW / 2}" y="${(yTop + hgt / 2 + 13).toFixed(1)}" text-anchor="middle" fill="#e6ebf5" font-size="11">${fmtMoney(value)}</text>`;
    } else if (hgt >= 15) {
      g += `<text x="${x + colW / 2}" y="${(yTop + hgt / 2 + 4).toFixed(1)}" text-anchor="middle" fill="#fff" font-size="10">${label} ${pct(value)}</text>`;
    }
  };
  // 左列: 資産 (上=流動資産, 下=固定資産)
  let y = PT + (ih - hOf(totA));
  seg(x1, y, hOf(curA), "#3b82f6", "流動資産", curA);
  seg(x1, y + hOf(curA), hOf(fixA), "#6366f1", "固定資産", fixA);
  g += `<text x="${x1 + colW / 2}" y="${H - 8}" text-anchor="middle" class="chart-tick">資産 ${fmtMoney(totA)}</text>`;
  // 右列: 流動負債 → 固定負債 → 純資産
  let y2 = PT + (ih - hOf(totL + Math.max(0, eq)));
  seg(x2, y2, hOf(curL), "#f59e0b", "流動負債", curL);
  seg(x2, y2 + hOf(curL), hOf(fixL), "#ef4444", "固定負債", fixL);
  if (eq >= 0) {
    seg(x2, y2 + hOf(totL), hOf(eq), "#22c55e", "純資産", eq);
  }
  g += `<text x="${x2 + colW / 2}" y="${H - 8}" text-anchor="middle" class="chart-tick">負債・純資産${eq < 0 ? " (債務超過)" : ""}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" role="img">${g}</svg>`;
}

// CFパターンの類型判定 (営業, 投資, 財務 の符号)
function cfPattern(o, i, f) {
  const key = (o >= 0 ? "+" : "-") + (i >= 0 ? "+" : "-") + (f >= 0 ? "+" : "-");
  const map = {
    "+--": ["健全・成熟型", "本業で稼ぎ、投資と返済/還元に回す理想形", "ok"],
    "+-+": ["成長投資型", "本業の稼ぎに加えて調達し、積極投資", "ok"],
    "++-": ["資産圧縮・返済型", "本業と資産売却で負債返済/還元", "market"],
    "+++": ["現金積上げ型", "あらゆる源泉で現金を積み上げ中", "market"],
    "---": ["蓄え取り崩し型", "本業赤字を過去の蓄えで補填 (要注意)", "warn"],
    "--+": ["調達依存型", "本業赤字・投資を外部調達で賄う (先行投資 or 要注意)", "warn"],
    "-+-": ["リストラ型", "本業赤字を資産売却で補い返済 (警戒)", "unread"],
    "-++": ["危険水準", "本業赤字を売却と調達の両方で補填", "unread"],
  };
  const [label, desc, cls] = map[key] || ["-", "", "market"];
  return { key, label, desc, cls };
}

function financialHealthHtml(stock, fin) {
  const b = fin.b || [];
  const c = fin.c || [];
  const lastB = b[b.length - 1];
  if (!lastB && !c.length) {
    return `<div class="card" style="margin-bottom:14px">
      <h2>💰 財務健全性 (BS/CF)</h2>
      <div class="empty">貸借対照表・キャッシュフローのデータは巡回取得中です(数時間〜1日で追加されます)。</div>
    </div>`;
  }

  // ネットキャッシュ比率 (清原式)
  let ncHtml = "";
  const nc = lastB ? netCashInfo(lastB, stock.market_cap) : null;
  if (nc) {
    ncHtml = `
    <div class="card" style="margin-bottom:14px">
      <h2>💰 ネットキャッシュ比率 <span class="count">清原式 (${h(periodLabel(lastB[0]))}期末)</span></h2>
      <div class="grid cols-4" style="grid-template-columns:repeat(3,1fr);margin-bottom:8px">
        <div class="card stat" style="background:var(--bg-elev)"><div class="label">ネットキャッシュ</div>
          <div class="value" style="font-size:20px;color:${nc.netCash >= 0 ? "#4ade80" : "#f87171"}">${fmtMoney(nc.netCash)}</div></div>
        <div class="card stat" style="background:var(--bg-elev)"><div class="label">ネットキャッシュ比率 (÷時価総額)</div>
          <div class="value" style="font-size:20px">${nc.ratio == null ? "-" : nc.ratio.toFixed(1) + "%"}</div></div>
        <div class="card stat" style="background:var(--bg-elev)"><div class="label">判定</div>
          <div style="margin-top:6px"><span class="badge ${nc.cls}" style="font-size:12px;padding:4px 10px">${h(nc.judge)}</span></div></div>
      </div>
      <div class="meta-line">ネットキャッシュ = 流動資産 ${fmtMoney(lastB[1])} + 投資有価証券${nc.hasInv ? ` ${fmtMoney(nc.inv)}` : "(データなし=0扱い)"}×70% − 負債合計 ${fmtMoney(lastB[4])}。比率100%超は「会社の換金価値が時価総額を上回る」水準。</div>
    </div>`;
  }

  // BS構成
  let bsHtml = "";
  if (lastB && lastB[2] != null) {
    const eq = lastB[2] - lastB[4];
    const eqRatio = lastB[2] > 0 ? (eq / lastB[2]) * 100 : null;
    bsHtml = `
    <div class="card" style="margin-bottom:14px">
      <h2>🏛 貸借対照表の構成 <span class="count">${h(periodLabel(lastB[0]))}期末 / 自己資本比率 ${eqRatio == null ? "-" : eqRatio.toFixed(1) + "%"}</span></h2>
      ${bsBoxSVG(lastB)}
      <div class="meta-line">流動資産 ${fmtMoney(lastB[1])} / 固定資産 ${fmtMoney(lastB[2] - lastB[1])} / 流動負債 ${fmtMoney(lastB[3])} / 固定負債 ${fmtMoney(lastB[4] - lastB[3])} / 純資産 ${fmtMoney(eq)}${eq < 0 ? ' <span class="badge unread">債務超過</span>' : ""}</div>
    </div>`;
  }

  // CF (符号と推移)
  let cfHtml = "";
  if (c.length) {
    const lastC = c[c.length - 1];
    const pat = cfPattern(lastC[1] || 0, lastC[2] || 0, lastC[3] || 0);
    const sign = (v) => v == null ? "<td>-</td>"
      : `<td class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "＋" : "−"} ${fmtMoney(Math.abs(v))}</td>`;
    const rows = [];
    for (let i = c.length - 1; i >= 0; i--) {
      const r = c[i];
      const fcf = r[1] != null && r[2] != null ? r[1] + r[2] : null;
      rows.push(`<tr><td>${h(periodLabel(r[0]))}</td>${sign(r[1])}${sign(r[2])}${sign(r[3])}${sign(fcf)}</tr>`);
    }
    cfHtml = `
    <div class="card" style="margin-bottom:14px">
      <h2>💸 キャッシュフロー <span class="count">直近パターン: 営業${lastC[1] >= 0 ? "＋" : "−"} 投資${lastC[2] >= 0 ? "＋" : "−"} 財務${lastC[3] >= 0 ? "＋" : "−"}</span></h2>
      <div style="margin-bottom:8px"><span class="badge ${pat.cls}" style="font-size:12px;padding:4px 12px">${h(pat.label)}</span>
        <span class="meta-line" style="display:inline;margin-left:8px">${h(pat.desc)}</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>期</th><th>営業CF</th><th>投資CF</th><th>財務CF</th><th>FCF (営業+投資)</th></tr></thead>
        <tbody>${rows.join("")}</tbody>
      </table></div>
    </div>`;
  }

  return ncHtml + bsHtml + cfHtml;
}

// 業績数値テーブル (年次/四半期、前期比つき)
function performanceTableHtml(rows, mode) {
  if (!rows.length) return "";
  const yoyLabel = mode === "q" ? "前年同期比" : "前期比";
  const back = mode === "q" ? 4 : 1;
  const yoyCell = (i, idx) => {
    const prev = rows[i - back];
    const v = yoyPct(rows[i][idx], prev && prev[idx]);
    if (v == null) return "<td>-</td>";
    return `<td class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</td>`;
  };
  const body = [];
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    const opm = r[1] && r[2] != null ? ((r[2] / r[1]) * 100).toFixed(1) + "%" : "-";
    body.push(`<tr>
      <td>${h(periodLabel(r[0]))}</td>
      <td class="num">${fmtMoney(r[1])}</td>${yoyCell(i, 1)}
      <td class="num">${fmtMoney(r[2])}</td>${yoyCell(i, 2)}
      <td class="num">${opm}</td>
      <td class="num">${fmtMoney(r[3])}</td>
      <td class="num">${r[4] == null ? "-" : r[4].toFixed(1) + "円"}</td>
    </tr>`);
  }
  return `
    <div style="margin-top:14px">
      <div class="chart-title">業績数値 (${mode === "q" ? "四半期" : "年次"})</div>
      <div class="table-wrap"><table>
        <thead><tr><th>期</th><th>売上高</th><th>${yoyLabel}</th><th>営業利益</th><th>${yoyLabel}</th><th>営業利益率</th><th>純利益</th><th>EPS</th></tr></thead>
        <tbody>${body.join("")}</tbody>
      </table></div>
    </div>`;
}

async function renderCompareBody() {
  const body = el("cmp_body");
  if (!body) return;
  if (!compareCodes.length) {
    body.innerHTML = '<div class="empty">比較する銘柄を追加してください (マイ銘柄からのクイック追加、またはコード入力)</div>';
    return;
  }
  const [findata, pricesData] = await Promise.all([loadFinancials(), loadPrices()]);
  const stocks = [];
  for (const c of compareCodes) {
    try {
      const s = await api.get("/stocks/" + encodeURIComponent(c));
      s._fin = findata.stocks[c] || { a: [], q: [] };
      s._price = pricesData.stocks[c] || null;
      stocks.push(s);
    } catch (e) { /* 除外 */ }
  }
  if (!stocks.length) {
    body.innerHTML = '<div class="empty">銘柄情報を取得できませんでした</div>';
    return;
  }

  const lastA = (s) => { const a = s._fin.a || []; return a[a.length - 1] || null; };
  const prevA = (s) => { const a = s._fin.a || []; return a[a.length - 2] || null; };
  const lastB = (s) => { const b = s._fin.b || []; return b[b.length - 1] || null; };
  const cell = (fn, cls) => stocks.map((s) => {
    const v = fn(s);
    return `<td class="${typeof cls === "function" ? cls(s) : (cls || "")}">${v}</td>`;
  }).join("");
  const yoyCell = (idx) => stocks.map((s) => {
    const l = lastA(s), p = prevA(s);
    const v = yoyPct(l && l[idx], p && p[idx]);
    if (v == null) return "<td>-</td>";
    return `<td class="${v >= 0 ? "pos" : "neg"}">${v >= 0 ? "+" : ""}${v.toFixed(1)}%</td>`;
  }).join("");
  const nextSched = (s) => {
    const today = new Date().toISOString().slice(0, 10);
    const n = (s.schedules || []).find((x) => x.announce_date >= today);
    return n ? `${fmtDate(n.announce_date)} ${h(n.fiscal_type || "")}` : "-";
  };

  const metricRows = `
    <tr><td class="metric-name">市場 / 業種</td>${cell((s) => `${h(s.market || "-")} / ${h(s.sector || "-")}`)}</tr>
    <tr><td class="metric-name">時価総額</td>${cell((s) => h(s.market_cap_label || "-"))}</tr>
    <tr><td class="metric-name">直近通期 (期末)</td>${cell((s) => { const l = lastA(s); return l ? h(l[0]) : "-"; })}</tr>
    <tr><td class="metric-name">売上高</td>${cell((s) => { const l = lastA(s); return fmtMoney(l && l[1]); })}</tr>
    <tr><td class="metric-name">売上高 前期比</td>${yoyCell(1)}</tr>
    <tr><td class="metric-name">営業利益</td>${cell((s) => { const l = lastA(s); return fmtMoney(l && l[2]); })}</tr>
    <tr><td class="metric-name">営業利益 前期比</td>${yoyCell(2)}</tr>
    <tr><td class="metric-name">営業利益率</td>${cell((s) => { const l = lastA(s); return fmtPct(l && l[1] && l[2] != null ? (l[2] / l[1]) * 100 : null); })}</tr>
    <tr><td class="metric-name">純利益</td>${cell((s) => { const l = lastA(s); return fmtMoney(l && l[3]); })}</tr>
    <tr><td class="metric-name">EPS</td>${cell((s) => { const l = lastA(s); return l && l[4] != null ? l[4].toFixed(1) + "円" : "-"; })}</tr>
    <tr><td class="metric-name">PER (概算)</td>${cell((s) => { const l = lastA(s); const ni = l && l[3]; return s.market_cap && ni > 0 ? (s.market_cap / ni).toFixed(1) + "倍" : "-"; })}</tr>
    <tr><td class="metric-name">PSR (概算)</td>${cell((s) => { const l = lastA(s); const rev = l && l[1]; return s.market_cap && rev > 0 ? (s.market_cap / rev).toFixed(2) + "倍" : "-"; })}</tr>
    <tr><td class="metric-name">ネットキャッシュ比率 (清原式)</td>${cell((s) => {
      const nc = netCashInfo(lastB(s), s.market_cap);
      return !nc || nc.ratio == null ? "-" : nc.ratio.toFixed(1) + "%";
    }, (s) => {
      const nc = netCashInfo(lastB(s), s.market_cap);
      return !nc || nc.ratio == null ? "" : nc.ratio >= 50 ? "pos" : nc.ratio < 0 ? "neg" : "";
    })}</tr>
    <tr><td class="metric-name">自己資本比率</td>${cell((s) => {
      const b = lastB(s);
      if (!b || b[2] == null || b[4] == null || b[2] <= 0) return "-";
      return (((b[2] - b[4]) / b[2]) * 100).toFixed(1) + "%";
    })}</tr>
    <tr><td class="metric-name">ROE</td>${cell((s) => {
      const l = lastA(s), b = lastB(s);
      const eq = b && b[2] != null && b[4] != null ? b[2] - b[4] : null;
      return l && l[3] != null && eq > 0 ? ((l[3] / eq) * 100).toFixed(1) + "%" : "-";
    })}</tr>
    <tr><td class="metric-name">配当利回り</td>${cell((s) =>
      s._price && s._price[6] != null ? s._price[6].toFixed(2) + "%" : "-")}</tr>
    <tr><td class="metric-name">次回決算予定</td>${cell(nextSched)}</tr>
    <tr><td class="metric-name">分析コメント</td>${cell((s) => {
      const c = analysisState.comments[s.code];
      return c ? h(c.slice(0, 60)) + (c.length > 60 ? "…" : "") : '<span style="color:var(--text-dim)">-</span>';
    })}</tr>`;

  // 推移チャート (年次): 全銘柄の期をマージした軸に揃える
  const allPeriods = [...new Set(stocks.flatMap((s) => (s._fin.a || []).map((r) => r[0])))].sort();
  const labels = allPeriods.map(periodLabel);
  const seriesOf = (idx, pctOfRev) => stocks.map((s, i) => {
    const byPeriod = new Map((s._fin.a || []).map((r) => [r[0], r]));
    return {
      name: `${s.code} ${s.name}`.slice(0, 16),
      color: CHART_COLORS[i % CHART_COLORS.length],
      type: "line",
      values: allPeriods.map((p) => {
        const r = byPeriod.get(p);
        if (!r) return null;
        if (pctOfRev) return r[1] && r[idx] != null ? (r[idx] / r[1]) * 100 : null;
        return r[idx];
      }),
    };
  });

  body.innerHTML = `
    <div class="table-wrap" style="margin-bottom:16px"><table>
      <thead><tr><th class="no-sort"></th>${stocks.map((s) =>
        `<th class="no-sort"><a class="link" href="#/analysis/${h(s.code)}">${h(s.code)} ${h(s.name)}</a></th>`).join("")}</tr></thead>
      <tbody>${metricRows}</tbody>
    </table></div>
    <div class="charts-grid">
      ${chartSVG({ title: "売上高の推移 (年次)", labels, series: seriesOf(1) })}
      ${chartSVG({ title: "営業利益率の推移 (年次)", labels, unit: "pct", series: seriesOf(2, true) })}
      ${chartSVG({ title: "営業利益の推移 (年次)", labels, series: seriesOf(2) })}
      ${chartSVG({ title: "純利益の推移 (年次)", labels, series: seriesOf(3) })}
    </div>
    <div class="meta-line">出典: Yahoo Finance の年次財務データ。銘柄名リンクから個別の銘柄分析へ移動できます。</div>`;
}

// ---------------------------------------------------------------------------
// 初期化
// ---------------------------------------------------------------------------
el("globalFetch").onclick = runFetch;
// GitHub Pages / file:// では最初からローカルモードで起動する
if (window.LocalApi && (location.protocol === "file:" || /\.github\.io$/.test(location.hostname))) {
  api.local = true;
  markLocalMode();
}
if (!location.hash) location.hash = "#/home";
render();
