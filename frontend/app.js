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
}
window.addEventListener("hashchange", render);

// ---------------------------------------------------------------------------
// ホーム画面
// ---------------------------------------------------------------------------
route("home", async (app) => {
  const d = await api.get("/home");
  const upcoming = d.registered_upcoming || [];
  const watch = d.watchlist || [];
  app.innerHTML = `
    <div class="page-head">
      <h1>ホーム</h1>
      <span class="sub">${fmtDate(d.date)} 時点</span>
    </div>
    <div class="grid cols-4" style="margin-bottom:16px">
      <div class="card stat"><div class="label">今日の決算予定</div><div class="value accent">${d.todays_count}</div></div>
      <div class="card stat"><div class="label">未確認の決算短信</div><div class="value warn">${d.unread_disclosures}</div></div>
      <div class="card stat"><div class="label">取得済み決算短信</div><div class="value ok">${d.fetched_total}</div></div>
      <div class="card stat"><div class="label">登録銘柄の直近予定</div><div class="value">${upcoming.length}</div></div>
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
    scheduleState.date_range = c.dataset.range;
    document.querySelectorAll("#dateChips .chip").forEach((x) => x.classList.toggle("active", x === c));
    loadSchedule();
  });
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
    Object.assign(scheduleState, { code: "", name: "", sector: "", market: "", cap_range: "", cap_min: "", cap_max: "" });
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
  if (!box) return;
  const arrow = (col) => scheduleState.sort === col ? (scheduleState.order === "asc" ? " ▲" : " ▼") : "";
  box.innerHTML = `
    <div class="page-head"><span class="sub">${d.count}件ヒット</span></div>
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
  const d = await api.get("/mystocks");
  app.innerHTML = `
    <div class="page-head"><h1>マイ銘柄</h1><span class="sub">${d.count}件登録中</span>
      <span style="margin-left:auto;display:inline-flex;gap:8px;align-items:center">
        <button class="btn small ghost" id="backupBtn" title="マイ銘柄・閲覧状態・分析コメント等をJSONファイルに保存">⬇ バックアップ</button>
        <button class="btn small ghost" id="restoreBtn" title="バックアップJSONから復元">⬆ 復元</button>
        <input type="file" id="restoreFile" accept=".json,application/json" style="display:none">
        <button class="fetch-btn" id="fetchMy">⟳ 決算短信を取得</button>
      </span></div>
    <div class="meta-line" style="margin-bottom:12px">マイ銘柄・閲覧状態・分析コメントはこのブラウザにのみ保存されます。端末変更やキャッシュクリアに備えて、定期的にバックアップしてください。</div>
    ${d.count === 0 ? '<div class="empty">まだ銘柄が登録されていません。<a class="link" href="#/schedule">決算予定一覧</a>から登録しましょう。</div>' : `
    <div class="table-wrap"><table>
      <thead><tr><th>コード</th><th>銘柄名</th><th>保有区分</th><th>重要度</th><th>次回決算</th><th>取得状況</th><th>メモ</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(myStockRow).join("")}</tbody>
    </table></div>`}`;
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

function myStockRow(s) {
  const unread = s.unread_count ? ` <span class="badge unread">未読${s.unread_count}</span>` : "";
  return `<tr>
    <td class="code-cell"><a class="link" href="#/stock/${h(s.code)}">${h(s.code)}</a></td>
    <td>${h(s.name)}</td>
    <td><span class="badge market">${h(s.holding_type || "")}</span></td>
    <td><span class="star">${stars(s.importance)}</span></td>
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
  const [d, caps] = await Promise.all([
    api.get("/disclosures" + (qs ? "?" + qs : "")),
    api.get("/cap-ranges"),
  ]);
  const chip = (key, label) =>
    `<span class="chip ${disclosureState.filter === key ? "active" : ""}" data-filter="${key}">${label}</span>`;
  app.innerHTML = `
    <div class="page-head"><h1>決算短信</h1><span class="sub">${d.count}件</span></div>
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
});

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
];

function downloadBackup() {
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
    if (!payload || payload.app !== "kessan-navi" || !payload.data) {
      toast("決算ナビのバックアップファイルではありません", true);
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
  };
  reader.readAsText(file);
}

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
  const checkboxes = my.items.map((m) =>
    `<label><input type="checkbox" value="${h(m.code)}" checked> ${h(m.code)} ${h(m.name)}</label>`).join("");

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
    <div class="card" style="margin-bottom:16px">
      <h2>📥 決算短信のまとめて取得（直近2年分）</h2>
      ${my.count ? `
        <div class="meta-line">対象のマイ銘柄を選んで、開示アーカイブ(GitHub Actionsが定期構築)から決算短信・訂正短信・業績予想修正・決算説明資料の履歴を取得します。</div>
        <div class="checklist" id="an_checklist">${checkboxes}</div>
        <button class="btn" id="an_bulk">選択した銘柄の決算短信をまとめて取得</button>
        <div class="fetch-status" id="an_bulk_status"></div>
        <div class="meta-line">※ TDnet本体の掲載期間(約1ヶ月)を過ぎた資料はPDF本体が削除されているため、タイトル表示となり 🔎 からWeb検索できます。アーカイブは過去2年分へ向けて自動構築中で、取得結果はこのブラウザに保存されます。</div>
      ` : '<div class="empty">マイ銘柄が未登録です。<a class="link" href="#/schedule">決算予定</a>から登録すると、まとめて取得できます。</div>'}
    </div>
    <div class="card" style="margin-bottom:16px">
      <h2>📌 PDF恒久保存リスト <span class="count" id="pdf_wl_count"></span></h2>
      <div class="meta-line">リストの銘柄は、決算短信PDF本体をGitHubリポジトリへ自動保存します(TDnetの掲載期間約1ヶ月を過ぎても、5年後でも閲覧可能)。容量制限のため50銘柄までを推奨。</div>
      <div id="pdf_wl_codes" style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">読み込み中…</div>
      <a class="btn small ghost" target="_blank" rel="noopener"
         href="https://github.com/kojit1229/stock_analyze/edit/main/config/pdf_watchlist.json">GitHubでリストを編集</a>
    </div>
    <div id="an_body">${code ? '<div class="loading">読み込み中…</div>' : '<div class="empty">銘柄を選択してください</div>'}</div>`;

  // PDF保存リストの表示 (config はリポジトリ直下 → 静的配信では ../config/)
  (async () => {
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
    if (!codes.length) {
      box.innerHTML = '<span class="meta-line">リストを取得できませんでした (GitHub上の config/pdf_watchlist.json を参照)</span>';
      return;
    }
    const cnt = el("pdf_wl_count");
    if (cnt) cnt.textContent = `${codes.length}銘柄`;
    box.innerHTML = codes.map((c) => {
      const saved = ((idx.codes || {})[c] || []).length;
      return `<span class="badge reg" style="font-size:12px;padding:4px 10px">${h(c)}${saved ? ` (${saved}件保存済)` : ""}</span>`;
    }).join(" ");
  })();

  const show = () => {
    const c = el("an_select").value || el("an_code").value.trim();
    if (c) location.hash = "#/analysis/" + encodeURIComponent(c);
  };
  el("an_show").onclick = show;
  el("an_select").onchange = show;
  const bulkBtn = el("an_bulk");
  if (bulkBtn) {
    bulkBtn.onclick = async () => {
      const codes = [...document.querySelectorAll("#an_checklist input:checked")].map((x) => x.value);
      if (!codes.length) { toast("銘柄を選択してください", true); return; }
      bulkBtn.disabled = true;
      try {
        await bulkFetchHistory(codes, el("an_bulk_status"));
        if (code) renderAnalysisBody(code);
      } finally {
        bulkBtn.disabled = false;
      }
    };
  }
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
  const findata = await loadFinancials();
  const metaInfo = await loadMetaInfo();
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
    ${last ? analysisInsightHtml(stock, fin) : ""}
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

// 決算シグナル + 投資指標カード (銘柄分析タブ)
function analysisInsightHtml(stock, fin) {
  const a = fin.a || [];
  const n = a.length;
  const last = a[n - 1];
  const signals = buildSignals(a);
  const sigHtml = signals.length
    ? signals.map((s) => `<span class="badge ${s.good ? "ok" : "unread"}" style="font-size:12px;padding:4px 12px">${h(s.label)}</span>`).join(" ")
    : '<span class="meta-line">判定に必要な期数が不足しています</span>';

  const rev = last && last[1], op = last && last[2], ni = last && last[3];
  const cap = stock.market_cap;
  const per = cap && ni > 0 ? cap / ni : null;
  const psr = cap && rev > 0 ? cap / rev : null;
  const nim = rev && ni != null ? (ni / rev) * 100 : null;
  const yrs = Math.min(3, n - 1);
  const revCagr = yrs >= 2 ? cagrPct(a[n - 1 - yrs][1], last[1], yrs) : null;
  const opCagr = yrs >= 2 ? cagrPct(a[n - 1 - yrs][2], last[2], yrs) : null;
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
    </div>`;
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
  const findata = await loadFinancials();
  const stocks = [];
  for (const c of compareCodes) {
    try {
      const s = await api.get("/stocks/" + encodeURIComponent(c));
      s._fin = findata.stocks[c] || { a: [], q: [] };
      stocks.push(s);
    } catch (e) { /* 除外 */ }
  }
  if (!stocks.length) {
    body.innerHTML = '<div class="empty">銘柄情報を取得できませんでした</div>';
    return;
  }

  const lastA = (s) => { const a = s._fin.a || []; return a[a.length - 1] || null; };
  const prevA = (s) => { const a = s._fin.a || []; return a[a.length - 2] || null; };
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
