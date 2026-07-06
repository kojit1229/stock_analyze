"""文字起こし結果のファイル出力。

- 録音開始時にファイルを作成し、発話確定ごとに逐次追記 + flush する
  (アプリ異常終了時もそれまでの内容が残る: F-08)。
- 停止時に全行を時系列ソートし、ヘッダー(開始/終了/所要時間)付きで
  整形して確定保存する。
- config.markdown_output が true なら同名 .md も出力する (F-13)。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CHANNEL_LABELS = {"self": "自分", "other": "相手"}


class TranscriptWriter:
    """スレッドセーフな逐次追記ライター。"""

    def __init__(self, output_dir: str, markdown_output: bool = False):
        self._dir = Path(output_dir).expanduser()
        self._markdown = markdown_output
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._file = None
        self.path: Path | None = None
        self._started_at: datetime | None = None

    def start(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._started_at = datetime.now()
        stamp = self._started_at.strftime("%Y%m%d_%H%M")
        self.path = self._unique_path(stamp)
        self._file = open(self.path, "a", encoding="utf-8")
        self._file.write(
            f"会議文字起こし(記録中) 開始: "
            f"{self._started_at.strftime('%Y-%m-%d %H:%M')}\n"
        )
        self._file.flush()
        logger.info("文字起こしファイルを作成: %s", self.path)
        return self.path

    def add(self, entry: dict) -> None:
        """entry: {"channel": "self"|"other", "start_ts": float, "text": str}"""
        line = format_line(entry)
        with self._lock:
            if self._file is None:
                return
            self._entries.append(entry)
            self._file.write(line + "\n")
            self._file.flush()

    def stop(self) -> Path | None:
        """整形して確定保存し、ファイルパスを返す。"""
        with self._lock:
            if self._file is None:
                return None
            self._file.close()
            self._file = None

            ended_at = datetime.now()
            entries = sorted(self._entries, key=lambda e: e["start_ts"])
            header = self._build_header(ended_at)
            body = "\n".join(format_line(e) for e in entries)
            assert self.path is not None
            self.path.write_text(header + body + "\n", encoding="utf-8")

            if self._markdown:
                md_path = self.path.with_suffix(".md")
                md_path.write_text(
                    self._build_markdown(ended_at, entries), encoding="utf-8"
                )
                logger.info("Markdown を保存: %s", md_path)
            logger.info("文字起こしを確定保存: %s (%d発話)", self.path, len(entries))
            return self.path

    def _build_header(self, ended_at: datetime) -> str:
        assert self._started_at is not None
        return (
            "会議文字起こし\n"
            f"開始: {self._started_at.strftime('%Y-%m-%d %H:%M')}  "
            f"終了: {ended_at.strftime('%Y-%m-%d %H:%M')}  "
            f"({format_duration(ended_at - self._started_at)})\n"
            + "-" * 40 + "\n"
        )

    def _build_markdown(self, ended_at: datetime, entries: list[dict]) -> str:
        assert self._started_at is not None
        lines = [
            "# 会議文字起こし",
            "",
            f"- 開始: {self._started_at.strftime('%Y-%m-%d %H:%M')}",
            f"- 終了: {ended_at.strftime('%Y-%m-%d %H:%M')}",
            f"- 所要: {format_duration(ended_at - self._started_at)}",
            "",
        ]
        for e in entries:
            ts = datetime.fromtimestamp(e["start_ts"]).strftime("%H:%M:%S")
            label = CHANNEL_LABELS.get(e["channel"], e["channel"])
            lines.append(f"- `{ts}` **[{label}]** {e['text']}")
        return "\n".join(lines) + "\n"

    def _unique_path(self, stamp: str) -> Path:
        path = self._dir / f"transcript_{stamp}.txt"
        n = 2
        while path.exists():
            path = self._dir / f"transcript_{stamp}_{n}.txt"
            n += 1
        return path


def format_line(entry: dict) -> str:
    ts = datetime.fromtimestamp(entry["start_ts"]).strftime("%H:%M:%S")
    label = CHANNEL_LABELS.get(entry["channel"], entry["channel"])
    return f"[{ts}] [{label}] {entry['text']}"


def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}時間{minutes}分"
    return f"{minutes}分"
