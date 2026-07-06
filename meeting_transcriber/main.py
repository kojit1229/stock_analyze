"""オンライン会議リアルタイム文字起こしツール エントリポイント。

音声・テキストはローカルにのみ保存し、外部サービスへは送信しない。
(初回のみ Whisper / VAD モデルのダウンロードで通信が発生する)
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import load_config  # noqa: E402


def setup_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


def main() -> int:
    setup_logging()
    logger = logging.getLogger("main")
    logger.info("起動")

    config = load_config()

    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("会議文字起こし")
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        logging.getLogger("main").exception("未処理の例外で終了")
        raise
