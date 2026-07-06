"""UI 部品: トランスクリプト表示とデバイス選択。"""

from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtWidgets import QComboBox, QTextBrowser

# 話者ごとの行の色 (視認性のため自分/相手で変える)
CHANNEL_COLORS = {"self": "#1565c0", "other": "#2e7d32"}
CHANNEL_LABELS = {"self": "自分", "other": "相手"}

FOLLOW_DEFAULT_LABEL = "既定の再生デバイスに追従"
DEFAULT_MIC_LABEL = "既定のマイク"


class TranscriptView(QTextBrowser):
    """発話を時系列順に表示するビュー。

    2ch の認識結果は完了順に届くため、start_ts で常にソートして保持し、
    順序が前後した場合のみ全体を再描画する。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self._entries: list[dict] = []

    def clear_entries(self) -> None:
        self._entries = []
        self.clear()

    def add_entry(self, entry: dict) -> None:
        at_bottom = self._is_at_bottom()
        if not self._entries or entry["start_ts"] >= self._entries[-1]["start_ts"]:
            self._entries.append(entry)
            self.append(_entry_html(entry))
        else:
            # 認識完了が前後した場合: 挿入位置を探して全再描画 (稀)
            self._entries.append(entry)
            self._entries.sort(key=lambda e: e["start_ts"])
            self.setHtml("".join(f"<p>{_entry_html(e)}</p>" for e in self._entries))
        if at_bottom:
            self._scroll_to_bottom()

    def _is_at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 10

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


def _entry_html(entry: dict) -> str:
    ts = datetime.fromtimestamp(entry["start_ts"]).strftime("%H:%M:%S")
    label = CHANNEL_LABELS.get(entry["channel"], entry["channel"])
    color = CHANNEL_COLORS.get(entry["channel"], "#333333")
    text = html.escape(entry["text"])
    return (
        f'<span style="color:#888888">{ts}</span> '
        f'<span style="color:{color}"><b>[{label}]</b> {text}</span>'
    )


class DeviceCombo(QComboBox):
    """デバイス選択コンボ。userData にデバイス名 (str) または None を持つ。

    None は「既定に追従」を意味する。
    """

    def __init__(self, follow_label: str, parent=None):
        super().__init__(parent)
        self._follow_label = follow_label

    def populate(self, names: list[str], selected: str | None) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem(self._follow_label, None)
        for name in names:
            self.addItem(name, name)
        if selected is not None:
            idx = self.findData(selected)
            self.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.setCurrentIndex(0)
        self.blockSignals(False)

    def selected_name(self) -> str | None:
        return self.currentData()
