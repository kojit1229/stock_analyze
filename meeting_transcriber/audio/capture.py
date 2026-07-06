"""キャプチャスレッド (マイク / WASAPI ループバック共通)。

デバイスのネイティブレートで開き、16kHz mono float32 にリサンプルして
VAD セグメンタへ渡す。確定したセグメントは ASR キューへ投入する。

Bluetooth 切断などでストリームエラーが起きたら、デバイスを再解決して
指数バックオフ (2s, 4s, 8s, 16s, 32s / 最大5回) で再接続を試みる。
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Callable

import numpy as np
from scipy.signal import resample_poly

from .vad import SAMPLE_RATE, Segmenter, SileroVAD

logger = logging.getLogger(__name__)

MAX_RECONNECT_TRIES = 5
CHUNK_SECONDS = 0.1          # 1回の read の長さ
FOLLOW_CHECK_INTERVAL = 5.0  # 既定デバイス追従の確認間隔(秒)
ACTIVE_RMS_THRESHOLD = 1e-3  # 「音が来ている」とみなす RMS


class CaptureThread(threading.Thread):
    """1系統(マイク or ループバック)のキャプチャ + VAD セグメント化。

    Parameters
    ----------
    channel:
        "self" (マイク) または "other" (ループバック)。
    device_resolver:
        pyaudio インスタンスを受け取りデバイス info を返す callable。
        「既定デバイスに追従」はここで毎回既定を引き直すことで実現する。
    segment_queue:
        確定セグメントの投入先。
        {"channel", "audio", "start_ts"} の dict を put する。
    """

    def __init__(
        self,
        channel: str,
        device_resolver: Callable,
        segment_queue: queue.Queue,
        vad_config: dict,
        follow_default: bool = False,
        on_status: Callable[[str], None] | None = None,
    ):
        super().__init__(daemon=True, name=f"capture-{channel}")
        self.channel = channel
        self._resolver = device_resolver
        self._queue = segment_queue
        self._vad_config = vad_config
        self._follow_default = follow_default
        self._on_status = on_status or (lambda msg: None)
        self._stop_event = threading.Event()
        self.last_active_ts = time.time()
        self.failed = False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        import pyaudiowpatch as pyaudio

        try:
            vad = SileroVAD()
        except Exception as e:  # noqa: BLE001 - モデル取得/ロード失敗
            logger.exception("[%s] VAD モデルの初期化に失敗", self.channel)
            self._on_status(f"VAD初期化失敗: {e}")
            self.failed = True
            return
        segmenter = Segmenter(
            vad,
            min_silence_ms=self._vad_config.get("min_silence_ms", 600),
            min_speech_ms=self._vad_config.get("min_speech_ms", 250),
            max_segment_s=self._vad_config.get("max_segment_s", 30),
            speech_threshold=self._vad_config.get("speech_threshold", 0.5),
            pre_roll_ms=self._vad_config.get("pre_roll_ms", 200),
        )

        retries = 0
        while not self._stop_event.is_set():
            pa = None
            stream = None
            try:
                pa = pyaudio.PyAudio()
                device = self._resolver(pa)
                if device is None:
                    raise OSError("対象デバイスが見つかりません")
                rate = int(device["defaultSampleRate"])
                channels = max(1, min(2, int(device["maxInputChannels"])))
                frames = max(1, int(rate * CHUNK_SECONDS))
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device["index"],
                    frames_per_buffer=frames,
                )
                logger.info(
                    "[%s] キャプチャ開始: %s (%dHz/%dch)",
                    self.channel, device["name"], rate, channels,
                )
                self._on_status(f"{device['name']} からキャプチャ中")
                retries = 0
                self._read_loop(
                    pa, stream, segmenter, device, rate, channels, frames
                )
                break  # 正常停止
            except OSError as e:
                if self._stop_event.is_set():
                    break
                retries += 1
                if retries > MAX_RECONNECT_TRIES:
                    logger.error("[%s] 再接続を諦めました: %s", self.channel, e)
                    self._on_status(
                        "デバイスに接続できません。デバイス設定を確認してください"
                    )
                    self.failed = True
                    break
                wait = 2 ** retries  # 2, 4, 8, 16, 32
                logger.warning(
                    "[%s] ストリームエラー (%s)。%d秒後に再接続 (%d/%d)",
                    self.channel, e, wait, retries, MAX_RECONNECT_TRIES,
                )
                self._on_status(f"デバイス切断を検知。再接続します ({retries}回目)")
                if self._stop_event.wait(wait):
                    break
            finally:
                if stream is not None:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except OSError:
                        pass
                if pa is not None:
                    pa.terminate()

        # 停止時: 進行中のセグメントを確定
        for audio, start_ts in segmenter.flush():
            self._put_segment(audio, start_ts)

    def _read_loop(self, pa, stream, segmenter, device, rate, channels, frames):
        """ストリームからの読み取りループ。デバイス切替時は OSError を投げる。"""
        up, down = _resample_ratio(rate)
        last_follow_check = time.time()
        while not self._stop_event.is_set():
            data = stream.read(frames, exception_on_overflow=False)
            audio = np.frombuffer(data, dtype=np.int16)
            audio = audio.astype(np.float32) / 32768.0
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            if rate != SAMPLE_RATE:
                audio = resample_poly(audio, up, down).astype(np.float32)

            if len(audio) and np.sqrt(np.mean(audio ** 2)) > ACTIVE_RMS_THRESHOLD:
                self.last_active_ts = time.time()

            for seg_audio, start_ts in segmenter.feed(audio):
                self._put_segment(seg_audio, start_ts)

            # 既定デバイス追従: 既定の再生先が変わったら開き直す
            if (
                self._follow_default
                and time.time() - last_follow_check > FOLLOW_CHECK_INTERVAL
            ):
                last_follow_check = time.time()
                current = self._resolver(pa)
                if current is not None and current["index"] != device["index"]:
                    logger.info(
                        "[%s] 既定デバイスが変更されたため開き直します: %s",
                        self.channel, current["name"],
                    )
                    raise OSError("default device changed")

    def _put_segment(self, audio: np.ndarray, start_ts: float) -> None:
        self._queue.put(
            {"channel": self.channel, "audio": audio, "start_ts": start_ts}
        )


def _resample_ratio(src_rate: int) -> tuple[int, int]:
    g = math.gcd(SAMPLE_RATE, src_rate)
    return SAMPLE_RATE // g, src_rate // g
