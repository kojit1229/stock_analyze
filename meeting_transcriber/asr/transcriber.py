"""faster-whisper による ASR ワーカースレッド。

モデルは 1 インスタンスを共有し、セグメントキューの順に処理する。
2ch (自分/相手) のセグメントが同時に来てもキュー順で逐次認識し、
時系列の整列は表示側 (start_ts ソート) が担当する。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# キャプチャ側が音を拾えていても Whisper が無音と判断した場合などに
# 出やすい定型ハルシネーションは捨てる
_HALLUCINATION_TEXTS = {
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございました。",
    "おやすみなさい",
    "。",
    "…",
}


class ASRWorker(threading.Thread):
    """セグメントキューを消費して文字起こし結果をコールバックへ渡す。"""

    def __init__(
        self,
        segment_queue: queue.Queue,
        config: dict,
        on_result: Callable[[dict], None],
        on_status: Callable[[str], None] | None = None,
    ):
        super().__init__(daemon=True, name="asr-worker")
        self._queue = segment_queue
        self._config = config
        self._on_result = on_result
        self._on_status = on_status or (lambda msg: None)
        self._stop_event = threading.Event()
        self.model_ready = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def drain(self, timeout: float = 60.0) -> bool:
        """キューが空になるまで待つ (停止時の取りこぼし防止)。"""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.empty():
                return True
            time.sleep(0.2)
        return False

    def run(self) -> None:
        from faster_whisper import WhisperModel

        model_name = self._config.get("model", "small")
        self._on_status(f"モデル読み込み中: {model_name} …")
        try:
            model = WhisperModel(
                model_name,
                device=self._config.get("device", "cpu"),
                compute_type=self._config.get("compute_type", "int8"),
            )
        except Exception as e:  # noqa: BLE001 - モデル起因の例外は多様
            logger.exception("モデルの読み込みに失敗")
            self._on_status(f"モデル読み込み失敗: {e}")
            return
        self.model_ready.set()
        self._on_status(
            f"認識中 ({model_name}/{self._config.get('compute_type', 'int8')})"
        )

        while True:
            try:
                seg = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            try:
                self._transcribe(model, seg)
            except Exception:  # noqa: BLE001 - 1セグメント失敗で止めない
                logger.exception("セグメントの認識に失敗")
            finally:
                self._queue.task_done()

    def _transcribe(self, model, seg: dict) -> None:
        segments, _info = model.transcribe(
            seg["audio"],
            language=self._config.get("language", "ja"),
            initial_prompt=self._config.get("initial_prompt") or None,
            beam_size=int(self._config.get("beam_size", 2)),
            vad_filter=False,  # 上流で silero-vad 済み
            condition_on_previous_text=False,
        )
        text = "".join(s.text for s in segments).strip()
        if not text or text in _HALLUCINATION_TEXTS:
            return
        self._on_result(
            {
                "channel": seg["channel"],
                "start_ts": seg["start_ts"],
                "text": text,
            }
        )
