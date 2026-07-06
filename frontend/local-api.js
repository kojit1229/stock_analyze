"use strict";
/* 決算ナビ ローカルAPI (静的ホスティング用)
 *
 * GitHub Pages などサーバなしの静的ホスティングで全機能を動かすための、
 * バックエンド (kessan/) と同等のロジックのブラウザ内実装。
 * - 銘柄・決算予定: kessan/seed.py と同じサンプルデータを毎回生成
 * - マイ銘柄・決算短信: localStorage に永続化
 * - PDF: kessan/pdfgen.py と同じ最小PDFをその場で生成し Blob URL で表示
 *
 * app.js は通常 /api/* へ fetch し、失敗した場合のみここへフォールバックする。
 * (Node でのテスト用に window が無い環境では globalThis に取り付ける)
 */
(function (global) {
  // -------------------------------------------------------------------------
  // 時価総額レンジ (kessan/market_cap.py と同一)
  // -------------------------------------------------------------------------
  const OKU = 1e8;
  const CHO = 1e12;
  const RANGES = [
    { key: "lt100oku", label: "100億円未満", min: 0, max: 100 * OKU },
    { key: "100to300oku", label: "100億円以上〜300億円未満", min: 100 * OKU, max: 300 * OKU },
    { key: "300to1000oku", label: "300億円以上〜1,000億円未満", min: 300 * OKU, max: 1000 * OKU },
    { key: "1000to3000oku", label: "1,000億円以上〜3,000億円未満", min: 1000 * OKU, max: 3000 * OKU },
    { key: "3000okuto1cho", label: "3,000億円以上〜1兆円未満", min: 3000 * OKU, max: CHO },
    { key: "gte1cho", label: "1兆円以上", min: CHO, max: null },
  ];

  function classify(cap) {
    if (cap == null) return null;
    for (const r of RANGES) {
      if (cap >= r.min && (r.max === null || cap < r.max)) return r.key;
    }
    return null;
  }

  function formatOku(cap) {
    if (cap == null) return "-";
    if (cap >= CHO) return (cap / CHO).toFixed(2) + "兆円";
    return Math.round(cap / OKU).toLocaleString("en-US") + "億円";
  }

  // -------------------------------------------------------------------------
  // サンプルデータ (kessan/seed.py と同一)
  // -------------------------------------------------------------------------
  const STOCKS = [
    ["7203", "トヨタ自動車", "プライム", "輸送用機器", 40e12, "内国株式"],
    ["6758", "ソニーグループ", "プライム", "電気機器", 18e12, "内国株式"],
    ["9984", "ソフトバンクグループ", "プライム", "情報・通信業", 13e12, "内国株式"],
    ["6861", "キーエンス", "プライム", "電気機器", 15e12, "内国株式"],
    ["9432", "日本電信電話", "プライム", "情報・通信業", 14e12, "内国株式"],
    ["8035", "東京エレクトロン", "プライム", "電気機器", 13e12, "内国株式"],
    ["6098", "リクルートホールディングス", "プライム", "サービス業", 12e12, "内国株式"],
    ["4063", "信越化学工業", "プライム", "化学", 11e12, "内国株式"],
    ["3382", "セブン&アイ・ホールディングス", "プライム", "小売業", 5e12, "内国株式"],
    ["7532", "パン・パシフィック・インターナショナルホールディングス", "プライム", "小売業", 2.5e12, "内国株式"],
    ["3092", "ZOZO", "プライム", "小売業", 1.1e12, "内国株式"],
    ["3697", "SHIFT", "プライム", "情報・通信業", 320e9, "内国株式"],
    ["4385", "メルカリ", "プライム", "情報・通信業", 380e9, "内国株式"],
    ["6027", "弁護士ドットコム", "グロース", "情報・通信業", 55e9, "内国株式"],
    ["4485", "JTOWER", "グロース", "情報・通信業", 62e9, "内国株式"],
    ["2158", "FRONTEO", "グロース", "情報・通信業", 21e9, "内国株式"],
    ["3853", "アステリア", "スタンダード", "情報・通信業", 8.5e9, "内国株式"],
    ["7351", "グッドパッチ", "グロース", "サービス業", 7e9, "内国株式"],
    ["4382", "ＨＥＲＯＺ", "グロース", "情報・通信業", 14e9, "内国株式"],
    ["2412", "ベネフィット・ワン", "プライム", "サービス業", 240e9, "内国株式"],
    ["6501", "日立製作所", "プライム", "電気機器", 16e12, "内国株式"],
    ["8306", "三菱ＵＦＪフィナンシャル・グループ", "プライム", "銀行業", 20e12, "内国株式"],
    ["4661", "オリエンタルランド", "プライム", "サービス業", 6e12, "内国株式"],
    ["6178", "日本郵政", "プライム", "サービス業", 480e9, "内国株式"],
  ];
  const FISCAL_TYPES = ["本決算", "第1四半期", "第2四半期", "第3四半期"];
  const ANNOUNCE_TIMES = ["引け後", "寄付前", "15:00", "未定"];
  const OFFSETS = [0, 0, 1, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 13, 14, 15, 17, 18, 20, 22, 24, 26, 28, 29];

  // -------------------------------------------------------------------------
  // 日付ユーティリティ
  // -------------------------------------------------------------------------
  const pad = (n) => String(n).padStart(2, "0");
  function isoDate(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }
  function todayISO() {
    return isoDate(new Date());
  }
  function addDaysISO(base, n) {
    const d = new Date(base + "T00:00:00");
    d.setDate(d.getDate() + n);
    return isoDate(d);
  }
  function nowISO() {
    const d = new Date();
    return `${isoDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // 日付レンジ (kessan/models.py date_range_bounds と同一。週の起点は月曜)
  function dateRangeBounds(key) {
    const t = new Date();
    const today = isoDate(t);
    const wd = (t.getDay() + 6) % 7; // 月=0 .. 日=6
    if (key === "today") return [today, today];
    if (key === "tomorrow") { const d = addDaysISO(today, 1); return [d, d]; }
    if (key === "this_week") return [today, addDaysISO(today, 6 - wd)];
    if (key === "next_week") { const s = addDaysISO(today, 7 - wd); return [s, addDaysISO(s, 6)]; }
    if (key === "month") return [today, addDaysISO(today, 30)];
    return [null, null];
  }

  // -------------------------------------------------------------------------
  // 状態 (localStorage 永続化。使えない環境ではメモリ内フォールバック)
  // -------------------------------------------------------------------------
  const STORE_KEY = "kessan_local_v1";
  const storage = (() => {
    try {
      const t = "__kessan_probe__";
      global.localStorage.setItem(t, "1");
      global.localStorage.removeItem(t);
      return global.localStorage;
    } catch (e) {
      let mem = {};
      return {
        getItem: (k) => (k in mem ? mem[k] : null),
        setItem: (k, v) => { mem[k] = String(v); },
        removeItem: (k) => { delete mem[k]; },
      };
    }
  })();

  function loadState() {
    try {
      const raw = storage.getItem(STORE_KEY);
      if (raw) {
        const s = JSON.parse(raw);
        if (s && Array.isArray(s.mystocks) && Array.isArray(s.disclosures)) return s;
      }
    } catch (e) { /* 壊れたデータは初期化 */ }
    return { mystocks: [], disclosures: [], nextDiscId: 1 };
  }
  function saveState() {
    storage.setItem(STORE_KEY, JSON.stringify(state));
  }
  let state = loadState();

  // ロード時に決算予定を生成 (seed.py と同様、今日を基準に相対配置)
  const LOAD_TIME = nowISO();
  const stocksByCode = new Map(STOCKS.map((s) => [s[0], {
    code: s[0], name: s[1], market: s[2], sector: s[3], market_cap: s[4],
    listing_type: s[5], updated_at: LOAD_TIME,
  }]));
  const SCHEDULE = STOCKS.map((s, i) => ({
    schedule_id: i + 1,
    code: s[0],
    announce_date: addDaysISO(todayISO(), OFFSETS[i]),
    fiscal_type: FISCAL_TYPES[i % FISCAL_TYPES.length],
    announce_time: ANNOUNCE_TIMES[i % ANNOUNCE_TIMES.length],
    source: "サンプルデータ",
    updated_at: LOAD_TIME,
  }));

  // -------------------------------------------------------------------------
  // 最小PDFジェネレータ (kessan/pdfgen.py の移植)
  // -------------------------------------------------------------------------
  function escPdf(t) {
    return String(t).replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  }

  function buildPdf(lines) {
    const parts = ["BT", "/F1 18 Tf", "1 0 0 1 60 760 Tm", "20 TL"];
    let first = true;
    for (const line of lines) {
      const s = escPdf(line);
      if (first) {
        parts.push(`(${s}) Tj`, "/F1 11 Tf", "16 TL", "T*");
        first = false;
      } else {
        parts.push(`(${s}) Tj`, "T*");
      }
    }
    parts.push("ET");
    const content = parts.join("\n");

    const objs = [
      "<< /Type /Catalog /Pages 2 0 R >>",
      "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
      `<< /Length ${content.length} >>\nstream\n${content}\nendstream`,
      "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ];
    let out = "%PDF-1.4\n";
    const offsets = [];
    objs.forEach((o, i) => {
      offsets.push(out.length);
      out += `${i + 1} 0 obj\n${o}\nendobj\n`;
    });
    const xref = out.length;
    const n = objs.length + 1;
    out += `xref\n0 ${n}\n0000000000 65535 f \n`;
    for (const off of offsets) out += String(off).padStart(10, "0") + " 00000 n \n";
    out += `trailer\n<< /Size ${n} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;

    const bytes = new Uint8Array(out.length);
    for (let i = 0; i < out.length; i++) bytes[i] = out.charCodeAt(i) & 0xff;
    return bytes;
  }

  // 銘柄コードから決定論的なサンプル業績値 (kessan/fetcher.py _figures と同一)
  function figures(code) {
    let base = 0;
    for (const c of code) if (c >= "0" && c <= "9") base += Number(c);
    base = base || 1;
    const revenue = base * 12345;
    const op = Math.floor(revenue * 0.11);
    const ne = Math.floor(revenue * 0.07);
    const eps = Math.round(base * 3.21 * 100) / 100;
    return { revenue, op, ne, eps };
  }

  function fmtNum(n) {
    return n.toLocaleString("en-US").padStart(15);
  }

  function disclosurePdfBytes(d) {
    const stock = stocksByCode.get(d.code) || { name: d.code };
    const f = figures(d.code);
    const lines = [
      `Kessan Tanshin (Financial Summary) - ${d.code}`,
      `Company Code: ${d.code}`,
      `Fiscal Period: ${d._fiscal || "-"}`,
      `Announcement Date: ${(d.published_at || "").slice(0, 10)}`,
      "",
      "--- Consolidated Results (million JPY) ---",
      `Net Sales:          ${fmtNum(f.revenue)}`,
      `Operating Profit:   ${fmtNum(f.op)}`,
      `Net Income:         ${fmtNum(f.ne)}`,
      `EPS (JPY):          ${f.eps.toFixed(2).padStart(15)}`,
      "",
      "(This is sample data generated in-browser for the static demo.)",
    ];
    return buildPdf(lines);
  }

  // -------------------------------------------------------------------------
  // 共通ヘルパー
  // -------------------------------------------------------------------------
  function enrich(d) {
    d.market_cap_label = formatOku(d.market_cap);
    d.cap_range = classify(d.market_cap);
    return d;
  }
  function registeredCodes() {
    return new Set(state.mystocks.map((m) => m.code));
  }
  function disclosureCountFor(code, date) {
    return state.disclosures.filter(
      (x) => x.code === code && (date == null || (x.published_at || "").slice(0, 10) === date)
    ).length;
  }
  function joinStock(d) {
    const s = stocksByCode.get(d.code) || {};
    return Object.assign({}, d, { name: s.name, market: s.market, sector: s.sector });
  }
  function lastUpdated() {
    let disc = null;
    for (const x of state.disclosures) if (!disc || x.fetched_at > disc) disc = x.fetched_at;
    return { schedule: LOAD_TIME, disclosure: disc };
  }
  function apiError(msg) {
    return new Error(msg);
  }

  // -------------------------------------------------------------------------
  // ハンドラ (kessan/models.py, api.py に対応)
  // -------------------------------------------------------------------------
  function listSchedule(q) {
    q = q || {};
    let [start, end] = dateRangeBounds(q.date_range || "all");
    let items = SCHEDULE.filter((es) => {
      const s = stocksByCode.get(es.code);
      if (start && es.announce_date < start) return false;
      if (end && es.announce_date > end) return false;
      if (q.date && es.announce_date !== q.date) return false;
      if (q.code && !es.code.includes(q.code)) return false;
      if (q.name && !s.name.includes(q.name)) return false;
      if (q.sector && s.sector !== q.sector) return false;
      if (q.market && s.market !== q.market) return false;
      // 時価総額: 任意レンジ優先、なければプリセット
      let lo = null, hi = null;
      if (q.cap_min != null && q.cap_min !== "") lo = Number(q.cap_min);
      if (q.cap_max != null && q.cap_max !== "") hi = Number(q.cap_max);
      if (lo === null && hi === null && q.cap_range) {
        const r = RANGES.find((x) => x.key === q.cap_range);
        if (r) { lo = r.min; hi = r.max; }
      }
      if (lo !== null && s.market_cap < lo) return false;
      if (hi !== null && s.market_cap >= hi) return false;
      return true;
    });

    const sortKey = { date: "announce_date", cap: "market_cap", code: "code", name: "name" }[q.sort] || "announce_date";
    const dir = (q.order || "asc").toLowerCase() === "desc" ? -1 : 1;
    const reg = registeredCodes();
    const rows = items.map((es) => {
      const s = stocksByCode.get(es.code);
      const cnt = disclosureCountFor(es.code, es.announce_date);
      return enrich({
        schedule_id: es.schedule_id, code: es.code, name: s.name, market: s.market,
        sector: s.sector, market_cap: s.market_cap,
        announce_date: es.announce_date, fiscal_type: es.fiscal_type,
        announce_time: es.announce_time, updated_at: es.updated_at,
        is_registered: reg.has(es.code),
        disclosure_count: cnt,
        fetch_status: cnt ? "取得済み" : "未取得",
      });
    });
    rows.sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av < bv) return -dir;
      if (av > bv) return dir;
      return a.code < b.code ? -1 : a.code > b.code ? 1 : 0;
    });
    return { count: rows.length, items: rows };
  }

  function stockDetail(code) {
    const s = stocksByCode.get(code);
    if (!s) throw apiError(`銘柄 ${code} が見つかりません`);
    const detail = enrich(Object.assign({}, s));
    detail.schedules = SCHEDULE.filter((es) => es.code === code)
      .slice().sort((a, b) => (a.announce_date < b.announce_date ? -1 : 1));
    detail.disclosures = state.disclosures.filter((x) => x.code === code)
      .slice().sort((a, b) => (a.published_at > b.published_at ? -1 : 1));
    const reg = state.mystocks.find((m) => m.code === code) || null;
    detail.registration = reg ? Object.assign({}, reg) : null;
    detail.is_registered = !!reg;
    return detail;
  }

  function listMyStocks() {
    const today = todayISO();
    const rows = state.mystocks.slice()
      .sort((a, b) => (b.importance - a.importance) || (a.registered_at < b.registered_at ? -1 : 1))
      .map((m) => {
        const s = stocksByCode.get(m.code) || {};
        const next = SCHEDULE.filter((es) => es.code === m.code && es.announce_date >= today)
          .sort((a, b) => (a.announce_date < b.announce_date ? -1 : 1))[0];
        const cnt = disclosureCountFor(m.code, null);
        const unread = state.disclosures.filter((x) => x.code === m.code && !x.is_read).length;
        return enrich(Object.assign({}, m, {
          name: s.name, market: s.market, sector: s.sector, market_cap: s.market_cap,
          next_announce_date: next ? next.announce_date : null,
          next_fiscal_type: next ? next.fiscal_type : null,
          disclosure_count: cnt,
          unread_count: unread,
          fetch_status: cnt ? "取得済み" : "未取得",
        }));
      });
    return { count: rows.length, items: rows };
  }

  function addMyStock(body) {
    const code = String((body && body.code) || "").trim();
    if (!code) throw apiError("code は必須です");
    if (!stocksByCode.has(code)) throw apiError(`銘柄コード ${code} は存在しません`);
    const existing = state.mystocks.find((m) => m.code === code);
    const fields = {
      holding_type: (body.holding_type || "監視中"),
      importance: Number(body.importance || 3),
      memo: body.memo || "",
      notify: body.notify ? 1 : 0,
    };
    if (existing) {
      Object.assign(existing, fields);
    } else {
      state.mystocks.push(Object.assign({
        user_id: "default", code, registered_at: nowISO(), last_checked_at: null,
      }, fields));
    }
    saveState();
    return stockDetail(code);
  }

  function updateMyStock(code, body) {
    const m = state.mystocks.find((x) => x.code === code);
    if (!m) throw apiError("登録銘柄が見つかりません");
    const allowed = ["holding_type", "importance", "memo", "notify", "last_checked_at"];
    for (const k of allowed) {
      if (body && k in body) {
        m[k] = (k === "importance" || k === "notify") ? Number(body[k]) : body[k];
      }
    }
    saveState();
    return stockDetail(code);
  }

  function deleteMyStock(code) {
    const i = state.mystocks.findIndex((x) => x.code === code);
    if (i < 0) throw apiError("登録銘柄が見つかりません");
    state.mystocks.splice(i, 1);
    saveState();
    return { deleted: true, code };
  }

  function listDisclosures(q) {
    q = q || {};
    let rows = state.disclosures.slice();
    if (q.code) rows = rows.filter((x) => x.code === q.code);
    if (q.unread === "1" || q.unread === "true" || q.unread === true) rows = rows.filter((x) => !x.is_read);
    if (q.doc_type) rows = rows.filter((x) => x.doc_type === q.doc_type);
    rows.sort((a, b) => (a.fetched_at > b.fetched_at ? -1 : a.fetched_at < b.fetched_at ? 1 : b.id - a.id));
    return { count: rows.length, items: rows.map(joinStock) };
  }

  function getDisclosure(id) {
    const d = state.disclosures.find((x) => x.id === Number(id));
    if (!d) throw apiError("決算短信が見つかりません");
    const s = stocksByCode.get(d.code) || {};
    return Object.assign({}, joinStock(d), { market_cap: s.market_cap });
  }

  function updateDisclosure(id, body) {
    const d = state.disclosures.find((x) => x.id === Number(id));
    if (!d) throw apiError("決算短信が見つかりません");
    if (body && "is_read" in body) d.is_read = body.is_read ? 1 : 0;
    if (body && "comment" in body) d.comment = body.comment;
    saveState();
    return getDisclosure(id);
  }

  // 決算短信の自動取得 (kessan/fetcher.py run_fetch と同等)
  function runFetch() {
    const today = todayISO();
    const now = nowISO();
    let fetched = 0;
    for (const m of state.mystocks) {
      const s = stocksByCode.get(m.code);
      for (const es of SCHEDULE) {
        if (es.code !== m.code || es.announce_date > today) continue;
        const title = `${es.announce_date} ${s.name}(${s.code}) ${es.fiscal_type} 決算短信〔日本基準〕`;
        const published = `${es.announce_date}T15:00:00`;
        // 重複取得防止 (要件9.3)
        const dup = state.disclosures.some(
          (x) => x.code === s.code && x.title === title && x.published_at === published
        );
        if (dup) continue;
        state.disclosures.push({
          id: state.nextDiscId++,
          code: s.code,
          title,
          pdf_url: "",
          pdf_path: `${s.code}_${es.announce_date}.pdf`,
          doc_type: "決算短信",
          published_at: published,
          fetched_at: now,
          is_read: 0,
          comment: "",
          _fiscal: es.fiscal_type, // PDF再生成用
        });
        fetched++;
      }
      m.last_checked_at = now;
    }
    saveState();
    return { fetched, message: `${fetched}件の決算短信を取得しました` };
  }

  function homeSummary() {
    const today = todayISO();
    const todays = listSchedule({ date: today });
    const reg = registeredCodes();
    const upcoming = SCHEDULE
      .filter((es) => reg.has(es.code) && es.announce_date >= today)
      .sort((a, b) => (a.announce_date < b.announce_date ? -1 : 1))
      .slice(0, 10)
      .map((es) => {
        const s = stocksByCode.get(es.code);
        return enrich({
          code: es.code, name: s.name, announce_date: es.announce_date,
          fiscal_type: es.fiscal_type, market_cap: s.market_cap,
        });
      });
    const watch = state.mystocks.slice()
      .sort((a, b) => b.importance - a.importance)
      .slice(0, 5)
      .map((m) => {
        const s = stocksByCode.get(m.code) || {};
        return enrich({ code: m.code, name: s.name, importance: m.importance, market_cap: s.market_cap });
      });
    return {
      date: today,
      todays_earnings: todays.items,
      todays_count: todays.count,
      registered_upcoming: upcoming,
      unread_disclosures: state.disclosures.filter((x) => !x.is_read).length,
      fetched_total: state.disclosures.length,
      watchlist: watch,
      last_updated: lastUpdated(),
    };
  }

  // -------------------------------------------------------------------------
  // ルーター (kessan/server.py ROUTES に対応)
  // -------------------------------------------------------------------------
  function handle(method, pathWithQuery, body) {
    const qi = pathWithQuery.indexOf("?");
    const path = qi >= 0 ? pathWithQuery.slice(0, qi) : pathWithQuery;
    const q = {};
    if (qi >= 0) {
      for (const [k, v] of new URLSearchParams(pathWithQuery.slice(qi + 1))) q[k] = v;
    }
    let m;

    if (method === "GET" && path === "/home") return homeSummary();
    if (method === "GET" && path === "/meta") return { last_updated: lastUpdated(), version: "0.1.0-static" };
    if (method === "GET" && path === "/sectors") {
      return { sectors: [...new Set(STOCKS.map((s) => s[3]))].sort() };
    }
    if (method === "GET" && path === "/markets") {
      return { markets: [...new Set(STOCKS.map((s) => s[2]))].sort() };
    }
    if (method === "GET" && path === "/cap-ranges") {
      return { ranges: RANGES.map((r) => ({ key: r.key, label: r.label, min: r.min, max: r.max })) };
    }
    if (method === "GET" && path === "/schedule") return listSchedule(q);
    if ((m = path.match(/^\/stocks\/([^/]+)$/)) && method === "GET") return stockDetail(m[1]);
    if (method === "GET" && path === "/mystocks") return listMyStocks();
    if (method === "POST" && path === "/mystocks") return addMyStock(body);
    if ((m = path.match(/^\/mystocks\/([^/]+)$/))) {
      if (method === "PATCH") return updateMyStock(m[1], body);
      if (method === "DELETE") return deleteMyStock(m[1]);
    }
    if (method === "GET" && path === "/disclosures") return listDisclosures(q);
    if ((m = path.match(/^\/disclosures\/(\d+)\/read$/)) && method === "POST") {
      const isRead = body && "is_read" in body ? body.is_read : true;
      return updateDisclosure(m[1], { is_read: isRead });
    }
    if ((m = path.match(/^\/disclosures\/(\d+)$/))) {
      if (method === "GET") return getDisclosure(m[1]);
      if (method === "PATCH") return updateDisclosure(m[1], body);
    }
    if (method === "POST" && path === "/fetch") return runFetch();
    throw apiError("not found: " + method + " " + path);
  }

  // PDF を Blob URL として返す (iframe / ダウンロード用)
  function pdfBlobUrl(id) {
    const bytes = pdfBytes(id);
    const blob = new Blob([bytes], { type: "application/pdf" });
    return URL.createObjectURL(blob);
  }

  function pdfBytes(id) {
    const d = state.disclosures.find((x) => x.id === Number(id));
    if (!d) throw apiError("決算短信が見つかりません");
    return disclosurePdfBytes(d);
  }

  // テスト用: 状態リセット
  function _reset() {
    state = { mystocks: [], disclosures: [], nextDiscId: 1 };
    saveState();
  }

  global.LocalApi = { handle, pdfBlobUrl, pdfBytes, _reset };
})(typeof window !== "undefined" ? window : globalThis);
