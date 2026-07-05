"""SQLite データベース接続とスキーマ管理。

標準ライブラリの sqlite3 のみを使用する。接続はリクエストごとに開閉する
シンプルな方式とし、外部キー制約を有効化する。
"""
import sqlite3

from . import config

SCHEMA = """
-- 6.1 銘柄データ
CREATE TABLE IF NOT EXISTS stocks (
    code         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    market       TEXT,
    sector       TEXT,
    market_cap   INTEGER,          -- 円
    listing_type TEXT,
    updated_at   TEXT NOT NULL
);

-- 6.2 決算予定データ
CREATE TABLE IF NOT EXISTS earnings_schedule (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL,
    announce_date TEXT NOT NULL,   -- YYYY-MM-DD
    fiscal_type   TEXT,            -- 本決算 / 第1四半期 など
    announce_time TEXT,            -- 寄付前 / 引け後 / 未定
    source        TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(code, announce_date, fiscal_type),
    FOREIGN KEY(code) REFERENCES stocks(code) ON DELETE CASCADE
);

-- 6.3 決算短信データ
CREATE TABLE IF NOT EXISTS disclosures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL,
    title        TEXT NOT NULL,
    pdf_url      TEXT,
    pdf_path     TEXT,             -- ローカル保存先ファイル名
    doc_type     TEXT,            -- 決算短信 / 訂正決算短信 / 業績予想修正 / 決算説明資料
    published_at TEXT,            -- 公開日時 ISO8601
    fetched_at   TEXT NOT NULL,   -- 取得日時 ISO8601
    is_read      INTEGER NOT NULL DEFAULT 0,
    comment      TEXT DEFAULT '',
    UNIQUE(code, title, published_at),
    FOREIGN KEY(code) REFERENCES stocks(code) ON DELETE CASCADE
);

-- 6.4 ユーザー登録銘柄データ (マイ銘柄)
CREATE TABLE IF NOT EXISTS my_stocks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL DEFAULT 'default',
    code           TEXT NOT NULL,
    holding_type   TEXT,          -- 保有中 / 監視中 / 売却済み / 気になる銘柄
    importance     INTEGER DEFAULT 3,   -- 1..5
    memo           TEXT DEFAULT '',
    notify         INTEGER NOT NULL DEFAULT 1,
    registered_at  TEXT NOT NULL,
    last_checked_at TEXT,
    UNIQUE(user_id, code),
    FOREIGN KEY(code) REFERENCES stocks(code) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_schedule_date ON earnings_schedule(announce_date);
CREATE INDEX IF NOT EXISTS idx_disclosures_code ON disclosures(code);
"""


def connect():
    """新しい DB 接続を返す。行は dict ライクにアクセスできる。"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """データディレクトリとテーブルを初期化する（冪等）。"""
    config.ensure_dirs()
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
