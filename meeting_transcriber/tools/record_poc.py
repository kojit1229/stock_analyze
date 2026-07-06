"""M1: 音声取得 PoC。

デバイス一覧の表示と、マイク + WASAPI ループバックの2系統同時録音を行う。

使い方 (meeting_transcriber ディレクトリで):
    python tools/record_poc.py --list            # デバイス一覧
    python tools/record_poc.py --seconds 15      # 15秒同時録音して wav 保存
    python tools/record_poc.py --seconds 15 --mic "ヘッドセット (…)"

Bluetooth イヤホンを接続し、Zoom テスト会議や動画再生など
「相手の音が鳴っている」状態で実行すること。
"""

from __future__ import annotations

import argparse
import sys
import threading
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audio import devices as dev  # noqa: E402


def list_devices(pa) -> None:
    print("=== 入力デバイス (マイク) ===")
    for info in dev.list_input_devices(pa):
        print(f"  [{info['index']:3d}] {info['name']}"
              f"  ({int(info['defaultSampleRate'])}Hz/{info['maxInputChannels']}ch)")
    print("=== ループバックデバイス (再生音キャプチャ) ===")
    for info in dev.list_loopback_devices(pa):
        print(f"  [{info['index']:3d}] {info['name']}"
              f"  ({int(info['defaultSampleRate'])}Hz/{info['maxInputChannels']}ch)")
    default_in = dev.get_default_input(pa)
    default_lb = dev.get_default_loopback(pa)
    print(f"既定マイク: {default_in['name'] if default_in else '(なし)'}")
    print(f"既定ループバック: {default_lb['name'] if default_lb else '(なし)'}")


def record(pa, pyaudio_mod, device: dict, seconds: int, out_path: Path) -> None:
    rate = int(device["defaultSampleRate"])
    channels = max(1, min(2, int(device["maxInputChannels"])))
    frames_per_buffer = rate // 10
    stream = pa.open(
        format=pyaudio_mod.paInt16,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=device["index"],
        frames_per_buffer=frames_per_buffer,
    )
    frames = []
    for _ in range(int(seconds * rate / frames_per_buffer)):
        frames.append(stream.read(frames_per_buffer, exception_on_overflow=False))
    stream.stop_stream()
    stream.close()

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))
    print(f"保存: {out_path} ({rate}Hz/{channels}ch/{seconds}s)")


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 音声取得PoC")
    parser.add_argument("--list", action="store_true", help="デバイス一覧を表示")
    parser.add_argument("--seconds", type=int, default=15, help="録音秒数")
    parser.add_argument("--mic", default=None, help="マイクデバイス名 (省略時は既定)")
    parser.add_argument("--loopback", default=None,
                        help="ループバックデバイス名 (省略時は既定)")
    parser.add_argument("--outdir", default="poc_out", help="wav 保存先")
    args = parser.parse_args()

    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    try:
        if args.list:
            list_devices(pa)
            return 0

        mic = (dev.find_device_by_name(dev.list_input_devices(pa), args.mic)
               if args.mic else dev.get_default_input(pa))
        loopback = (dev.find_device_by_name(dev.list_loopback_devices(pa),
                                            args.loopback)
                    if args.loopback else dev.get_default_loopback(pa))
        if mic is None or loopback is None:
            print("デバイスが見つかりません。--list で確認してください")
            return 1

        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        print(f"マイク: {mic['name']}")
        print(f"ループバック: {loopback['name']}")
        print(f"{args.seconds}秒間、2系統同時録音します…")

        threads = [
            threading.Thread(target=record, args=(
                pa, pyaudio, mic, args.seconds, outdir / "mic.wav")),
            threading.Thread(target=record, args=(
                pa, pyaudio, loopback, args.seconds, outdir / "loopback.wav")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        print("完了。wav を再生して両系統の音を確認してください。")
        return 0
    finally:
        pa.terminate()


if __name__ == "__main__":
    sys.exit(main())
