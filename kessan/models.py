"""データアクセス層（リポジトリ）。

API ハンドラから呼ばれる問い合わせ・更新関数を集約する。日付の絞り込みや
時価総額レンジの適用など、要件のドメインロジックはここに置く。
"""
import datetime

from . import config, db
from . import market_cap as mc


# ---------------------------------------------------------------------------
# 日付レンジ
# ---------------------------------------------------------------------------
def date_range_bounds(range_key, reference_date=None):
    """日付レンジキーから (start, end) の ISO 文字列を返す（両端含む）。

    None を返す端は「制限なし」を意味する。対応キー:
    today / tomorrow / this_week / next_week / month / all
    """
    today = reference_date or datetime.date.today()
    if range_key == "today":
        return today.isoformat(), today.isoformat()
    if range_key == "tomorrow":
        d = today + datetime.timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if range_key == "this_week":
        # 今日から今週末(日曜)まで
        end = today + datetime.timedelta(days=(6 - today.weekday()))
        return today.isoformat(), end.isoformat()
    if range_key == "next_week":
        start = today + datetime.timedelta(days=(7 - today.weekday()))
        end = start + datetime.timedelta(days=6)
        return start.isoformat(), end.isoformat()
    if range_key == "month":
        end = today + datetime.timedelta(days=30)
        return today.isoformat(), end.isoformat()
    # all / 未知
    return None, None


# ---------------------------------------------------------------------------
# 決算予定一覧 / 検索・絞り込み（要件 4.1, 4.2）
# ---------------------------------------------------------------------------
def list_schedule(filters=None, reference_date=None, user_id=None):
    filters = filters or {}
    uid = user_id or config.DEFAULT_USER_ID
    where = []
    params = []

    # 日付レンジ
    start, end = date_range_bounds(filters.get("date_range", "all"), reference_date)
    if start:
        where.append("es.announce_date >= ?")
        params.append(start)
    if end:
        where.append("es.announce_date <= ?")
        params.append(end)

    # 明示的な日付指定（決算発表日）
    if filters.get("date"):
        where.append("es.announce_date = ?")
        params.append(filters["date"])

    # 銘柄コード / 銘柄名（部分一致）
    if filters.get("code"):
        where.append("s.code LIKE ?")
        params.append(f"%{filters['code']}%")
    if filters.get("name"):
        where.append("s.name LIKE ?")
        params.append(f"%{filters['name']}%")

    # 業種 / 市場区分（完全一致）
    if filters.get("sector"):
        where.append("s.sector = ?")
        params.append(filters["sector"])
    if filters.get("market"):
        where.append("s.market = ?")
        params.append(filters["market"])

    # 時価総額レンジ
    cap_min, cap_max = _resolve_cap(filters)
    if cap_min is not None:
        where.append("s.market_cap >= ?")
        params.append(cap_min)
    if cap_max is not None:
        where.append("s.market_cap < ?")
        params.append(cap_max)

    # 並び替え
    sort = filters.get("sort", "date")
    order = "ASC" if filters.get("order", "asc").lower() == "asc" else "DESC"
    sort_col = {
        "date": "es.announce_date",
        "cap": "s.market_cap",
        "code": "s.code",
        "name": "s.name",
    }.get(sort, "es.announce_date")

    sql = f"""
        SELECT es.id AS schedule_id, s.code AS code, s.name AS name,
               s.market AS market, s.sector AS sector, s.market_cap AS market_cap,
               es.announce_date AS announce_date, es.fiscal_type AS fiscal_type,
               es.announce_time AS announce_time, es.updated_at AS updated_at
        FROM earnings_schedule es
        JOIN stocks s ON s.code = es.code
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY {sort_col} {order}, s.code ASC
    """

    conn = db.connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        registered = _registered_codes(conn, uid)
        result = []
        for r in rows:
            d = _enrich_stock(dict(r))
            d["is_registered"] = r["code"] in registered
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM disclosures WHERE code=? AND substr(published_at,1,10)=?",
                (r["code"], r["announce_date"]),
            ).fetchone()["c"]
            d["disclosure_count"] = cnt
            d["fetch_status"] = "取得済み" if cnt else "未取得"
            result.append(d)
        return result
    finally:
        conn.close()


def _resolve_cap(filters):
    """フィルタから時価総額の (min, max) を解決する。任意レンジを優先。"""
    cap_min = filters.get("cap_min")
    cap_max = filters.get("cap_max")
    if cap_min is not None or cap_max is not None:
        return (
            int(cap_min) if cap_min not in (None, "") else None,
            int(cap_max) if cap_max not in (None, "") else None,
        )
    key = filters.get("cap_range")
    if key:
        lo, hi = mc.range_bounds(key)
        return lo, hi
    return None, None


def _enrich_stock(d):
    d["market_cap_label"] = mc.format_oku(d.get("market_cap"))
    d["cap_range"] = mc.classify(d.get("market_cap"))
    return d


def _registered_codes(conn, uid):
    rows = conn.execute("SELECT code FROM my_stocks WHERE user_id=?", (uid,)).fetchall()
    return {r["code"] for r in rows}


# ---------------------------------------------------------------------------
# 銘柄詳細（要件 5.5）
# ---------------------------------------------------------------------------
def get_stock(code, user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM stocks WHERE code=?", (code,)).fetchone()
        if not row:
            return None
        stock = _enrich_stock(dict(row))

        schedules = conn.execute(
            "SELECT * FROM earnings_schedule WHERE code=? ORDER BY announce_date ASC",
            (code,),
        ).fetchall()
        stock["schedules"] = [dict(s) for s in schedules]

        disclosures = conn.execute(
            "SELECT * FROM disclosures WHERE code=? ORDER BY published_at DESC",
            (code,),
        ).fetchall()
        stock["disclosures"] = [dict(x) for x in disclosures]

        reg = conn.execute(
            "SELECT * FROM my_stocks WHERE user_id=? AND code=?", (uid, code)
        ).fetchone()
        stock["registration"] = dict(reg) if reg else None
        stock["is_registered"] = reg is not None
        return stock
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# マイ銘柄（要件 4.3, 5.4）
# ---------------------------------------------------------------------------
HOLDING_TYPES = ["保有中", "監視中", "売却済み", "気になる銘柄"]


def list_my_stocks(user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    today = datetime.date.today().isoformat()
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT ms.*, s.name AS name, s.market AS market, s.sector AS sector,
                   s.market_cap AS market_cap
            FROM my_stocks ms JOIN stocks s ON s.code = ms.code
            WHERE ms.user_id=?
            ORDER BY ms.importance DESC, ms.registered_at ASC
            """,
            (uid,),
        ).fetchall()
        result = []
        for r in rows:
            d = _enrich_stock(dict(r))
            nxt = conn.execute(
                "SELECT announce_date, fiscal_type FROM earnings_schedule"
                " WHERE code=? AND announce_date>=? ORDER BY announce_date ASC LIMIT 1",
                (r["code"], today),
            ).fetchone()
            d["next_announce_date"] = nxt["announce_date"] if nxt else None
            d["next_fiscal_type"] = nxt["fiscal_type"] if nxt else None
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM disclosures WHERE code=?", (r["code"],)
            ).fetchone()["c"]
            unread = conn.execute(
                "SELECT COUNT(*) AS c FROM disclosures WHERE code=? AND is_read=0",
                (r["code"],),
            ).fetchone()["c"]
            d["disclosure_count"] = cnt
            d["unread_count"] = unread
            d["fetch_status"] = "取得済み" if cnt else "未取得"
            result.append(d)
        return result
    finally:
        conn.close()


def add_my_stock(code, holding_type=None, importance=3, memo="", notify=1, user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM stocks WHERE code=?", (code,)).fetchone()
        if not exists:
            raise ValueError(f"銘柄コード {code} は存在しません")
        conn.execute(
            """
            INSERT INTO my_stocks (user_id, code, holding_type, importance, memo, notify, registered_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(user_id, code) DO UPDATE SET
                holding_type=excluded.holding_type,
                importance=excluded.importance,
                memo=excluded.memo,
                notify=excluded.notify
            """,
            (uid, code, holding_type or "監視中", int(importance), memo, int(bool(notify)), now),
        )
        conn.commit()
        return get_stock(code, uid)
    finally:
        conn.close()


def update_my_stock(code, fields, user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    allowed = {"holding_type", "importance", "memo", "notify", "last_checked_at"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(int(v) if k in ("importance", "notify") else v)
    if not sets:
        return None
    params.extend([uid, code])
    conn = db.connect()
    try:
        cur = conn.execute(
            f"UPDATE my_stocks SET {', '.join(sets)} WHERE user_id=? AND code=?", params
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return get_stock(code, uid)
    finally:
        conn.close()


def delete_my_stock(code, user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    conn = db.connect()
    try:
        cur = conn.execute(
            "DELETE FROM my_stocks WHERE user_id=? AND code=?", (uid, code)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 決算短信（要件 4.4, 4.5）
# ---------------------------------------------------------------------------
def list_disclosures(filters=None):
    filters = filters or {}
    where, params = [], []
    if filters.get("code"):
        where.append("d.code = ?")
        params.append(filters["code"])
    if filters.get("unread") in (True, "true", "1", 1):
        where.append("d.is_read = 0")
    if filters.get("doc_type"):
        where.append("d.doc_type = ?")
        params.append(filters["doc_type"])

    sql = f"""
        SELECT d.*, s.name AS name, s.market AS market, s.sector AS sector
        FROM disclosures d JOIN stocks s ON s.code = d.code
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY d.fetched_at DESC, d.id DESC
    """
    conn = db.connect()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_disclosure(disc_id):
    conn = db.connect()
    try:
        row = conn.execute(
            """
            SELECT d.*, s.name AS name, s.market AS market, s.sector AS sector,
                   s.market_cap AS market_cap
            FROM disclosures d JOIN stocks s ON s.code = d.code
            WHERE d.id=?
            """,
            (disc_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_disclosure(disc_id, fields):
    allowed = {"is_read", "comment"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(int(bool(v)) if k == "is_read" else v)
    if not sets:
        return get_disclosure(disc_id)
    params.append(disc_id)
    conn = db.connect()
    try:
        cur = conn.execute(f"UPDATE disclosures SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
        if cur.rowcount == 0:
            return None
        return get_disclosure(disc_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ホーム画面サマリ（要件 5.1）
# ---------------------------------------------------------------------------
def home_summary(reference_date=None, user_id=None):
    uid = user_id or config.DEFAULT_USER_ID
    today = (reference_date or datetime.date.today()).isoformat()
    conn = db.connect()
    try:
        # 今日の決算予定
        todays = list_schedule({"date": today}, reference_date, uid)

        # 登録銘柄の直近決算予定
        upcoming = conn.execute(
            """
            SELECT s.code AS code, s.name AS name, es.announce_date AS announce_date,
                   es.fiscal_type AS fiscal_type, s.market_cap AS market_cap
            FROM my_stocks ms
            JOIN stocks s ON s.code = ms.code
            JOIN earnings_schedule es ON es.code = ms.code
            WHERE ms.user_id=? AND es.announce_date >= ?
            ORDER BY es.announce_date ASC LIMIT 10
            """,
            (uid, today),
        ).fetchall()

        # 未確認(未閲覧)の決算短信
        unread = conn.execute(
            "SELECT COUNT(*) AS c FROM disclosures WHERE is_read=0"
        ).fetchone()["c"]
        total_disc = conn.execute(
            "SELECT COUNT(*) AS c FROM disclosures"
        ).fetchone()["c"]

        # 注目銘柄(重要度の高いマイ銘柄)
        watch = conn.execute(
            """
            SELECT s.code AS code, s.name AS name, ms.importance AS importance,
                   s.market_cap AS market_cap
            FROM my_stocks ms JOIN stocks s ON s.code = ms.code
            WHERE ms.user_id=?
            ORDER BY ms.importance DESC LIMIT 5
            """,
            (uid,),
        ).fetchall()

        return {
            "date": today,
            "todays_earnings": todays,
            "todays_count": len(todays),
            "registered_upcoming": [_enrich_stock(dict(r)) for r in upcoming],
            "unread_disclosures": unread,
            "fetched_total": total_disc,
            "watchlist": [_enrich_stock(dict(r)) for r in watch],
            "last_updated": meta_last_updated(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# メタ情報（最終更新日時など。要件 9.3）
# ---------------------------------------------------------------------------
def meta_last_updated():
    conn = db.connect()
    try:
        sched = conn.execute(
            "SELECT MAX(updated_at) AS m FROM earnings_schedule"
        ).fetchone()["m"]
        disc = conn.execute(
            "SELECT MAX(fetched_at) AS m FROM disclosures"
        ).fetchone()["m"]
        return {"schedule": sched, "disclosure": disc}
    finally:
        conn.close()


def list_sectors():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL ORDER BY sector"
        ).fetchall()
        return [r["sector"] for r in rows]
    finally:
        conn.close()


def list_markets():
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT market FROM stocks WHERE market IS NOT NULL ORDER BY market"
        ).fetchall()
        return [r["market"] for r in rows]
    finally:
        conn.close()
