"""silero-vad (ONNX) ラッパーとストリーミング用セグメンタ。

torch には依存せず、onnxruntime で silero_vad.onnx (v5) を直接実行する。
モデルファイルは models/silero_vad.onnx に置く。無ければ初回に
GitHub (snakers4/silero-vad) から自動ダウンロードする。
"""

from __future__ import annotations

import logging
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512  # silero-vad v5 は 16kHz で 512 サンプル固定
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 32ms

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_PATH = MODEL_DIR / "silero_vad.onnx"
MODEL_URLS = [
    "https://github.com/snakers4/silero-vad/raw/master/"
    "src/silero_vad/data/silero_vad.onnx",
    # GitHub に届かない環境向けミラー
    "https://cdn.jsdelivr.net/gh/snakers4/silero-vad@master/"
    "src/silero_vad/data/silero_vad.onnx",
]


def ensure_vad_model(path: Path = MODEL_PATH) -> Path:
    """VAD モデルが無ければダウンロードする(約2MB、初回のみ)。"""
    if path.exists() and path.stat().st_size > 100_000:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".onnx.tmp")
    last_error: Exception | None = None
    for url in MODEL_URLS:
        try:
            logger.info("silero-vad モデルをダウンロード中: %s", url)
            urllib.request.urlretrieve(url, tmp)
            if tmp.stat().st_size > 100_000:
                tmp.replace(path)
                logger.info("silero-vad モデルを保存: %s", path)
                return path
            last_error = OSError(f"ダウンロード内容が不正: {url}")
        except OSError as e:
            last_error = e
            logger.warning("ダウンロード失敗 (%s): %s", url, e)
    raise OSError(
        f"silero-vad モデルを取得できませんでした。手動で {MODEL_URLS[0]} を "
        f"{path} に保存してください"
    ) from last_error


class SileroVAD:
    """silero-vad v5 ONNX モデルの薄いラッパー。フレーム毎に発話確率を返す。"""

    def __init__(self, model_path: Path = MODEL_PATH):
        import onnxruntime as ort

        ensure_vad_model(model_path)
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        """512 サンプルの float32 モノラル波形の発話確率 (0..1)。"""
        if frame.shape[0] != FRAME_SAMPLES:
            raise ValueError(f"frame must be {FRAME_SAMPLES} samples")
        inputs = {
            "input": frame.reshape(1, -1).astype(np.float32),
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        }
        out, self._state = self._session.run(None, inputs)
        return float(out[0][0])


class Segmenter:
    """VAD 確率から発話セグメントを切り出すステートマシン。

    feed() に 16kHz mono float32 の波形を任意長で渡すと、確定した
    セグメントのリスト [(audio, start_wallclock_ts), ...] を返す。
    発話開始〜min_silence_ms の無音で 1 セグメント確定。
    max_segment_s を超えたら強制分割する。
    """

    def __init__(
        self,
        vad: SileroVAD,
        min_silence_ms: int = 600,
        min_speech_ms: int = 250,
        max_segment_s: float = 30.0,
        speech_threshold: float = 0.5,
        pre_roll_ms: int = 200,
    ):
        self._vad = vad
        self._threshold = speech_threshold
        # ヒステリシス: 終了判定は開始判定より緩くしてブツ切れを防ぐ
        self._exit_threshold = max(0.15, speech_threshold - 0.15)
        self._min_silence_frames = max(1, int(min_silence_ms / FRAME_MS))
        self._min_speech_frames = max(1, int(min_speech_ms / FRAME_MS))
        self._max_segment_frames = max(1, int(max_segment_s * 1000 / FRAME_MS))
        pre_roll_frames = max(1, int(pre_roll_ms / FRAME_MS))
        self._pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_frames)

        self._residual = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._speech_frames: list[np.ndarray] = []
        self._speech_frame_count = 0
        self._silence_count = 0
        self._segment_start_ts = 0.0

    def feed(self, audio: np.ndarray) -> list[tuple[np.ndarray, float]]:
        now = time.time()
        buf = np.concatenate([self._residual, audio.astype(np.float32)])
        n_frames = len(buf) // FRAME_SAMPLES
        self._residual = buf[n_frames * FRAME_SAMPLES:]

        # buf 末尾が「今」に対応するとみなし、各フレームの時刻を逆算する
        total_pending = len(buf)
        segments: list[tuple[np.ndarray, float]] = []
        for i in range(n_frames):
            frame = buf[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            samples_after_frame_start = total_pending - i * FRAME_SAMPLES
            frame_start_ts = now - samples_after_frame_start / SAMPLE_RATE
            segments.extend(self._process_frame(frame, frame_start_ts))
        return segments

    def flush(self) -> list[tuple[np.ndarray, float]]:
        """録音停止時に、進行中のセグメントを確定して返す。"""
        segments = []
        if self._in_speech and self._speech_frame_count >= self._min_speech_frames:
            segments.append(self._finalize())
        self._in_speech = False
        self._speech_frames = []
        self._speech_frame_count = 0
        self._silence_count = 0
        self._pre_roll.clear()
        self._vad.reset()
        return segments

    def _process_frame(
        self, frame: np.ndarray, frame_start_ts: float
    ) -> list[tuple[np.ndarray, float]]:
        prob = self._vad.prob(frame)
        segments: list[tuple[np.ndarray, float]] = []

        if not self._in_speech:
            if prob >= self._threshold:
                self._in_speech = True
                self._speech_frames = list(self._pre_roll) + [frame]
                self._speech_frame_count = 1
                self._silence_count = 0
                self._segment_start_ts = (
                    frame_start_ts - len(self._pre_roll) * FRAME_MS / 1000
                )
            else:
                self._pre_roll.append(frame)
            return segments

        self._speech_frames.append(frame)
        if prob >= self._exit_threshold:
            self._speech_frame_count += 1
            self._silence_count = 0
        else:
            self._silence_count += 1

        if self._silence_count >= self._min_silence_frames:
            if self._speech_frame_count >= self._min_speech_frames:
                segments.append(self._finalize())
            self._in_speech = False
            self._speech_frames = []
            self._speech_frame_count = 0
            self._silence_count = 0
        elif len(self._speech_frames) >= self._max_segment_frames:
            # 長い連続発話は強制分割し、続きは新セグメントとして継続
            segments.append(self._finalize())
            self._speech_frames = []
            self._speech_frame_count = 0
            self._silence_count = 0
            self._segment_start_ts = frame_start_ts
        return segments

    def _finalize(self) -> tuple[np.ndarray, float]:
        audio = np.concatenate(self._speech_frames)
        return audio, self._segment_start_ts
