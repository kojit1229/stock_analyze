"""メインウィンドウ。

録音開始/停止、デバイス選択、逐次表示、常に手前表示、保存先設定を担当。
ワーカースレッドからの通知はすべて Qt Signal 経由でメインスレッドに渡す。
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from asr.transcriber import ASRWorker
from audio import devices as dev
from audio.capture import CaptureThread
from config import save_config
from output.writer import TranscriptWriter

from .widgets import (
    DEFAULT_MIC_LABEL,
    FOLLOW_DEFAULT_LABEL,
    DeviceCombo,
    TranscriptView,
)

logger = logging.getLogger(__name__)

SILENCE_WARN_SECONDS = 30  # 相手音声がこの秒数無音なら警告 (デバイス不一致検知)


class Bridge(QObject):
    """ワーカースレッド → UI スレッドへの通知橋渡し。"""

    result = Signal(dict)
    status = Signal(str)
    stopped = Signal(str)  # 保存先パス ("" は保存なし)


class MainWindow(QMainWindow):
    def __init__(self, config: dict):
        super().__init__()
        self._config = config
        self._recording = False
        self._stopping = False
        self._started_at = 0.0
        self._captures: list[CaptureThread] = []
        self._loopback_capture: CaptureThread | None = None
        self._asr: ASRWorker | None = None
        self._writer: TranscriptWriter | None = None

        self._bridge = Bridge()
        self._bridge.result.connect(self._on_result)
        self._bridge.status.connect(self._set_status)
        self._bridge.stopped.connect(self._on_stopped)

        self._build_ui()
        self._apply_always_on_top(self._config.get("always_on_top", False))
        self._refresh_devices()

    # ---------- UI 構築 ----------

    def _build_ui(self) -> None:
        self.setWindowTitle("会議文字起こし")
        self.resize(680, 560)

        self._btn_toggle = QPushButton("● 録音開始")
        self._btn_toggle.setMinimumHeight(36)
        self._btn_toggle.clicked.connect(self._toggle_recording)

        self._lbl_elapsed = QLabel("経過 00:00:00")
        self._chk_top = QCheckBox("📌 手前表示")
        self._chk_top.setChecked(self._config.get("always_on_top", False))
        self._chk_top.toggled.connect(self._apply_always_on_top)

        top_row = QHBoxLayout()
        top_row.addWidget(self._btn_toggle)
        top_row.addWidget(self._lbl_elapsed)
        top_row.addStretch(1)
        top_row.addWidget(self._chk_top)

        self._combo_mic = DeviceCombo(DEFAULT_MIC_LABEL)
        self._combo_loopback = DeviceCombo(FOLLOW_DEFAULT_LABEL)
        btn_refresh = QPushButton("再検出")
        btn_refresh.clicked.connect(self._refresh_devices)

        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("マイク:"))
        mic_row.addWidget(self._combo_mic, 1)
        lb_row = QHBoxLayout()
        lb_row.addWidget(QLabel("相手音声:"))
        lb_row.addWidget(self._combo_loopback, 1)
        lb_row.addWidget(btn_refresh)

        self._view = TranscriptView()

        self._btn_outdir = QPushButton("保存先...")
        self._btn_outdir.clicked.connect(self._choose_output_dir)
        self._lbl_status = QLabel("待機中")
        self._lbl_status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._lbl_status, 1)
        bottom_row.addWidget(self._btn_outdir)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addLayout(mic_row)
        layout.addLayout(lb_row)
        layout.addWidget(self._view, 1)
        layout.addLayout(bottom_row)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)

        self._show_output_dir()

    # ---------- デバイス ----------

    def _refresh_devices(self) -> None:
        try:
            import pyaudiowpatch as pyaudio

            pa = pyaudio.PyAudio()
            try:
                mic_names = [d["name"] for d in dev.list_input_devices(pa)]
                lb_names = [d["name"] for d in dev.list_loopback_devices(pa)]
            finally:
                pa.terminate()
        except Exception as e:  # noqa: BLE001 - 非Windows等でも起動は許す
            logger.exception("デバイス列挙に失敗")
            self._set_status(f"デバイス列挙に失敗: {e}")
            mic_names, lb_names = [], []

        self._combo_mic.populate(mic_names, self._config.get("mic_device_name"))
        loopback_selected = (
            None
            if self._config.get("follow_default_output", True)
            else self._config.get("loopback_device_name")
        )
        self._combo_loopback.populate(lb_names, loopback_selected)

    def _make_mic_resolver(self, name: str | None):
        def resolver(pa):
            if name:
                found = dev.find_device_by_name(dev.list_input_devices(pa), name)
                if found:
                    return found
            return dev.get_default_input(pa)

        return resolver

    def _make_loopback_resolver(self, name: str | None):
        def resolver(pa):
            if name:
                found = dev.find_device_by_name(dev.list_loopback_devices(pa), name)
                if found:
                    return found
            return dev.get_default_loopback(pa)

        return resolver

    # ---------- 録音開始/停止 ----------

    def _toggle_recording(self) -> None:
        if self._stopping:
            return
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        mic_name = self._combo_mic.selected_name()
        lb_name = self._combo_loopback.selected_name()
        follow = lb_name is None

        # 選択内容を config に保存 (F-11)
        self._config["mic_device_name"] = mic_name
        self._config["loopback_device_name"] = lb_name
        self._config["follow_default_output"] = follow
        save_config(self._config)

        self._writer = TranscriptWriter(
            self._config["output_dir"],
            markdown_output=self._config.get("markdown_output", False),
        )
        try:
            self._writer.start()
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"保存先に書き込めません:\n{e}")
            self._writer = None
            return

        segment_queue: queue.Queue = queue.Queue()
        self._asr = ASRWorker(
            segment_queue,
            self._config,
            on_result=self._bridge.result.emit,
            on_status=self._bridge.status.emit,
        )
        self._asr.start()

        vad_conf = self._config.get("vad", {})
        mic_capture = CaptureThread(
            "self",
            self._make_mic_resolver(mic_name),
            segment_queue,
            vad_conf,
            on_status=lambda m: self._bridge.status.emit(f"マイク: {m}"),
        )
        loopback_capture = CaptureThread(
            "other",
            self._make_loopback_resolver(lb_name),
            segment_queue,
            vad_conf,
            follow_default=follow,
            on_status=lambda m: self._bridge.status.emit(f"相手音声: {m}"),
        )
        self._captures = [mic_capture, loopback_capture]
        self._loopback_capture = loopback_capture
        for c in self._captures:
            c.start()

        self._recording = True
        self._started_at = time.time()
        self._view.clear_entries()
        self._btn_toggle.setText("■ 録音停止")
        self._combo_mic.setEnabled(False)
        self._combo_loopback.setEnabled(False)
        self._btn_outdir.setEnabled(False)
        self._timer.start()

    def _stop_recording(self) -> None:
        self._stopping = True
        self._btn_toggle.setEnabled(False)
        self._set_status("停止処理中(認識待ちのセグメントを処理しています)…")
        threading.Thread(target=self._finish_stop, daemon=True).start()

    def _finish_stop(self) -> None:
        """UI を固めないよう別スレッドで停止処理を行う。"""
        for c in self._captures:
            c.stop()
        for c in self._captures:
            c.join(timeout=10)
        path = ""
        if self._asr is not None:
            self._asr.drain(timeout=120)
            self._asr.stop()
            self._asr.join(timeout=30)
        if self._writer is not None:
            saved = self._writer.stop()
            path = str(saved) if saved else ""
        self._bridge.stopped.emit(path)

    def _on_stopped(self, path: str) -> None:
        self._recording = False
        self._stopping = False
        self._captures = []
        self._loopback_capture = None
        self._asr = None
        self._writer = None
        self._timer.stop()
        self._btn_toggle.setEnabled(True)
        self._btn_toggle.setText("● 録音開始")
        self._combo_mic.setEnabled(True)
        self._combo_loopback.setEnabled(True)
        self._btn_outdir.setEnabled(True)
        if path:
            self._set_status(f"保存しました: {path}")
        else:
            self._set_status("停止しました")

    # ---------- コールバック ----------

    def _on_result(self, entry: dict) -> None:
        self._view.add_entry(entry)
        if self._writer is not None:
            self._writer.add(entry)

    def _on_tick(self) -> None:
        elapsed = int(time.time() - self._started_at)
        h, rest = divmod(elapsed, 3600)
        m, s = divmod(rest, 60)
        self._lbl_elapsed.setText(f"経過 {h:02d}:{m:02d}:{s:02d}")

        # 相手音声の無音警告 (ループバック対象の不一致検知)
        lc = self._loopback_capture
        if (
            lc is not None
            and not lc.failed
            and time.time() - lc.last_active_ts > SILENCE_WARN_SECONDS
        ):
            self._set_status(
                "⚠ 相手音声が30秒以上無音です。会議アプリの出力先と"
                "「相手音声」デバイスが一致しているか確認してください"
            )

    # ---------- その他 ----------

    def _apply_always_on_top(self, on: bool) -> None:
        self._config["always_on_top"] = bool(on)
        save_config(self._config)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()

    def _choose_output_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "保存先フォルダを選択", self._config.get("output_dir", "")
        )
        if chosen:
            self._config["output_dir"] = chosen
            save_config(self._config)
            self._show_output_dir()

    def _show_output_dir(self) -> None:
        self._set_status(f"待機中  保存先: {self._config.get('output_dir', '')}")

    def _set_status(self, message: str) -> None:
        self._lbl_status.setText(message)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt の命名
        if self._recording or self._stopping:
            # 録音中に閉じられた場合も追記済み txt は残る (F-08)。
            # 可能な範囲で整形保存を試みる。
            for c in self._captures:
                c.stop()
            for c in self._captures:
                c.join(timeout=3)
            if self._asr is not None:
                self._asr.drain(timeout=5)
                self._asr.stop()
                self._asr.join(timeout=5)
            if self._writer is not None:
                self._writer.stop()
        event.accept()
