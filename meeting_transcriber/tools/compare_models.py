"""M2: バッチ文字起こし・モデル比較。

M1 で録音した wav を複数モデルで文字起こしし、精度(目視)と
処理速度(実時間比 = 処理時間 / 音声長)を比較する。
実時間比が 0.5 を切っていればリアルタイム処理に十分な余裕がある。

使い方:
    python tools/compare_models.py poc_out/loopback.wav
    python tools/compare_models.py poc_out/loopback.wav ^
        --models small kotoba-tech/kotoba-whisper-v2.0-faster
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

DEFAULT_MODELS = ["small", "kotoba-tech/kotoba-whisper-v2.0-faster"]


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def main() -> int:
    parser = argparse.ArgumentParser(description="M2 モデル比較")
    parser.add_argument("wav", nargs="+", help="対象 wav ファイル")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--language", default="ja")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    results = []
    for model_name in args.models:
        print(f"\n===== モデル: {model_name} ({args.compute_type}/{args.device}) =====")
        t0 = time.perf_counter()
        model = WhisperModel(
            model_name, device=args.device, compute_type=args.compute_type
        )
        print(f"読み込み: {time.perf_counter() - t0:.1f}s")

        for wav_path in args.wav:
            path = Path(wav_path)
            duration = wav_duration(path)
            t0 = time.perf_counter()
            segments, _info = model.transcribe(
                str(path), language=args.language, beam_size=2
            )
            texts = [s.text for s in segments]  # generator を消費して計測
            elapsed = time.perf_counter() - t0
            ratio = elapsed / duration if duration else float("inf")
            results.append((model_name, path.name, duration, elapsed, ratio))

            print(f"\n--- {path.name} (音声長 {duration:.1f}s) ---")
            print("".join(texts).strip() or "(認識結果なし)")
            print(f"処理時間: {elapsed:.1f}s  実時間比: {ratio:.2f}"
                  + ("  ← リアルタイム余裕あり" if ratio < 0.5 else ""))

    print("\n===== まとめ =====")
    print(f"{'モデル':<45} {'ファイル':<15} {'音声長':>7} {'処理':>7} {'実時間比':>7}")
    for model_name, fname, duration, elapsed, ratio in results:
        print(f"{model_name:<45} {fname:<15} {duration:>6.1f}s {elapsed:>6.1f}s {ratio:>7.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
