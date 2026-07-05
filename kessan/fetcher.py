"""決算短信の自動取得ロジック（要件 4.4）。

実運用では TDnet / 企業IR 等をクロールするが、MVP では外部接続を行わず
`MockDisclosureSource` がサンプル PDF を生成する。取得処理は差し替え可能な
`DisclosureSource` インターフェースを介して行うため、将来 TDnet 実装へ
置き換えられる。

取得ポリシー:
- マイ銘柄(登録銘柄)のみを対象とする。
- 決算予定日が基準日以前(発表済み)の予定について決算短信を取得する。
- 同一資料の重複取得は UNIQUE 制約と存在チェックで防ぐ(要件 9.3)。
"""
import datetime
import os

from . import config, db
from . import pdfgen


class DisclosureSource:
    """決算短信取得ソースのインターフェース。"""

    def fetch_for(self, stock, schedule):
        """銘柄と決算予定に対応する開示資料メタデータのリストを返す。

        各要素は dict: {title, doc_type, published_at, pdf_bytes}
        """
        raise NotImplementedError


class MockDisclosureSource(DisclosureSource):
    """サンプル PDF を生成するモックソース。"""

    def _figures(self, code):
        """銘柄コードから決定論的なサンプル業績値(百万円)を作る。"""
        base = sum(int(c) for c in code if c.isdigit()) or 1
        revenue = base * 12_345
        op = int(revenue * 0.11)
        ne = int(revenue * 0.07)
        eps = round(base * 3.21, 2)
        return revenue, op, ne, eps

    def fetch_for(self, stock, schedule):
        code = stock["code"]
        name = stock["name"]
        fiscal = schedule["fiscal_type"] or "決算"
        date = schedule["announce_date"]
        revenue, op, ne, eps = self._figures(code)
        title = f"{date} {name}({code}) {fiscal} 決算短信〔日本基準〕"
        published = f"{date}T15:00:00"

        # ASCII の本文(日本語フォント埋め込みを避けるためローマ字/英数字表記)
        lines = [
            f"Kessan Tanshin (Financial Summary) - {code}",
            f"Company Code: {code}",
            f"Fiscal Period: {fiscal}",
            f"Announcement Date: {date}",
            "",
            "--- Consolidated Results (million JPY) ---",
            f"Net Sales:          {revenue:>15,}",
            f"Operating Profit:   {op:>15,}",
            f"Net Income:         {ne:>15,}",
            f"EPS (JPY):          {eps:>15,.2f}",
            "",
            "(This is sample data generated for the MVP viewer.)",
        ]
        pdf_bytes = pdfgen.build_pdf(lines)
        return [
            {
                "title": title,
                "doc_type": "決算短信",
                "published_at": published,
                "pdf_bytes": pdf_bytes,
            }
        ]


def _disclosure_exists(conn, code, title, published_at):
    row = conn.execute(
        "SELECT 1 FROM disclosures WHERE code=? AND title=? AND published_at=?",
        (code, title, published_at),
    ).fetchone()
    return row is not None


def run_fetch(source=None, reference_date=None, user_id=None):
    """登録銘柄の決算短信を取得する。取得した件数を返す。

    source: DisclosureSource 実装（省略時は MockDisclosureSource）。
    reference_date: この日付以前の決算予定を発表済みとして扱う。
    """
    source = source or MockDisclosureSource()
    ref = reference_date or datetime.date.today()
    ref_str = ref.isoformat()
    uid = user_id or config.DEFAULT_USER_ID
    config.ensure_dirs()
    now = datetime.datetime.now().replace(microsecond=0).isoformat()

    conn = db.connect()
    fetched = 0
    try:
        # 登録銘柄と、発表済み(基準日以前)の決算予定を取得
        rows = conn.execute(
            """
            SELECT s.code AS code, s.name AS name,
                   es.announce_date AS announce_date, es.fiscal_type AS fiscal_type
            FROM my_stocks ms
            JOIN stocks s ON s.code = ms.code
            JOIN earnings_schedule es ON es.code = ms.code
            WHERE ms.user_id = ? AND es.announce_date <= ?
            """,
            (uid, ref_str),
        ).fetchall()

        for row in rows:
            stock = {"code": row["code"], "name": row["name"]}
            schedule = {
                "announce_date": row["announce_date"],
                "fiscal_type": row["fiscal_type"],
            }
            for item in source.fetch_for(stock, schedule):
                if _disclosure_exists(conn, stock["code"], item["title"], item["published_at"]):
                    continue  # 重複取得防止(要件 9.3)
                filename = f"{stock['code']}_{item['published_at'][:10]}_{fetched}.pdf"
                path = os.path.join(config.PDF_DIR, filename)
                with open(path, "wb") as f:
                    f.write(item["pdf_bytes"])
                conn.execute(
                    """
                    INSERT INTO disclosures
                        (code, title, pdf_url, pdf_path, doc_type, published_at, fetched_at, is_read, comment)
                    VALUES (?,?,?,?,?,?,?,0,'')
                    """,
                    (
                        stock["code"],
                        item["title"],
                        item.get("pdf_url", ""),
                        filename,
                        item["doc_type"],
                        item["published_at"],
                        now,
                    ),
                )
                fetched += 1

            # マイ銘柄の最終確認日時を更新
            conn.execute(
                "UPDATE my_stocks SET last_checked_at=? WHERE user_id=? AND code=?",
                (now, uid, stock["code"]),
            )
        conn.commit()
    finally:
        conn.close()
    return fetched
