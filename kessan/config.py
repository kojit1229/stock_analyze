"""アプリケーション全体の設定値。

環境変数で上書き可能なパスや定数をここに集約する。
"""
import os

# プロジェクトルート（このファイルの1つ上の階層）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# データ保存ディレクトリ
DATA_DIR = os.environ.get("KESSAN_DATA_DIR", os.path.join(BASE_DIR, "data"))

# SQLite データベースファイル
DB_PATH = os.environ.get("KESSAN_DB_PATH", os.path.join(DATA_DIR, "kessan.db"))

# 取得した決算短信 PDF の保存先
PDF_DIR = os.environ.get("KESSAN_PDF_DIR", os.path.join(DATA_DIR, "pdfs"))

# フロントエンド静的ファイルのディレクトリ
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# 単一ユーザー運用のためのデフォルトユーザーID（MVP）
DEFAULT_USER_ID = "default"


def ensure_dirs():
    """データ保存に必要なディレクトリを作成する。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PDF_DIR, exist_ok=True)
