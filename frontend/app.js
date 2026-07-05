"use strict";
/* 決算ナビ フロントエンド (依存ライブラリなしのバニラ JS SPA) */

// ---------------------------------------------------------------------------
// API クライアント
// ---------------------------------------------------------------------------
const api = {
  async req(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const res = await fetch("/api" + path, opt);
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) throw new Error((data && data.error) || res.statusText);
    return data;
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
    <td>${fmtDate(s.announce_date)} <span class="badge market">${h(s.announce_time || "")}</span></td>
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
      <span style="margin-left:auto"><button class="fetch-btn" id="fetchMy">⟳ 決算短信を取得</button></span></div>
    ${d.count === 0 ? '<div class="empty">まだ銘柄が登録されていません。<a class="link" href="#/schedule">決算予定一覧</a>から登録しましょう。</div>' : `
    <div class="table-wrap"><table>
      <thead><tr><th>コード</th><th>銘柄名</th><th>保有区分</th><th>重要度</th><th>次回決算</th><th>取得状況</th><th>メモ</th><th>操作</th></tr></thead>
      <tbody>${d.items.map(myStockRow).join("")}</tbody>
    </table></div>`}`;
  const fm = el("fetchMy");
  if (fm) fm.onclick = () => runFetch();
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
const disclosureState = { unread: false, code: "" };

route("disclosures", async (app, rest) => {
  if (rest && rest.length) return disclosureDetail(app, rest[0]);
  const q = disclosureState.unread ? "?unread=1" : "";
  const d = await api.get("/disclosures" + q);
  app.innerHTML = `
    <div class="page-head"><h1>決算短信</h1><span class="sub">${d.count}件</span></div>
    <div class="chips">
      <span class="chip ${!disclosureState.unread ? "active" : ""}" id="chipAll">すべて</span>
      <span class="chip ${disclosureState.unread ? "active" : ""}" id="chipUnread">未閲覧のみ</span>
    </div>
    ${d.count === 0 ? '<div class="empty">取得済みの決算短信がありません。マイ銘柄を登録して「⟳ 取得」を実行してください。</div>' : `
    <div class="table-wrap"><table>
      <thead><tr><th>状態</th><th>コード</th><th>銘柄名</th><th>種別</th><th>タイトル</th><th>公開日時</th><th>取得日時</th><th></th></tr></thead>
      <tbody>${d.items.map(disclosureRow).join("")}</tbody>
    </table></div>`}`;
  el("chipAll").onclick = () => { disclosureState.unread = false; render(); };
  el("chipUnread").onclick = () => { disclosureState.unread = true; render(); };
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
  const pdfUrl = `/api/disclosures/${id}/pdf`;
  app.innerHTML = `
    <a class="back-link" href="#/disclosures">← 決算短信一覧へ戻る</a>
    <div class="page-head"><h1>${h(d.title)}</h1></div>
    <div class="grid cols-2" style="grid-template-columns:2fr 1fr">
      <div class="card">
        <div class="pdf-toolbar">
          <a class="btn small" href="${pdfUrl}" target="_blank">🔍 外部ブラウザで開く</a>
          <a class="btn small ghost" href="${pdfUrl}?download=1">⬇ ダウンロード</a>
        </div>
        <iframe class="pdf-frame" src="${pdfUrl}" title="PDF"></iframe>
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
// 初期化
// ---------------------------------------------------------------------------
el("globalFetch").onclick = runFetch;
if (!location.hash) location.hash = "#/home";
render();
